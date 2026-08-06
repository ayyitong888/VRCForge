//! Windows approval-toast boundary.
//!
//! Permission scope: the current VRCForge desktop process may display a toast
//! and emit a UI event only. Lifecycle: Windows may retain the display after
//! the approval ends, but every action is accepted only while that exact
//! approval remains pending. Authentication: the event is merely a user-intent
//! signal; the existing App session plus pending-approval/project-scope checks
//! must authenticate and authorize any later approve or reject request.

use serde::{Deserialize, Serialize};
#[cfg(windows)]
use std::path::{Path, PathBuf};
use tauri::{AppHandle, Emitter, Manager};
#[cfg(windows)]
use windows_sys::Win32::{
    Foundation::ERROR_SUCCESS,
    System::Registry::{
        RegCloseKey, RegCreateKeyExW, RegSetValueExW, HKEY_CURRENT_USER, KEY_SET_VALUE,
        REG_OPTION_NON_VOLATILE, REG_SZ,
    },
    UI::Shell::SetCurrentProcessExplicitAppUserModelID,
};

const APPROVAL_NOTIFICATION_APP_ID: &str = "app.vrcforge.agentic";
const APPROVAL_NOTIFICATION_DISPLAY_NAME: &str = "VRCForge";
const APPROVAL_NOTIFICATION_ICON_BACKGROUND_COLOR: &str = "0";
const APPROVAL_NOTIFICATION_ICON_FILE_NAME: &str = "VRCForge.png";
const APPROVAL_NOTIFICATION_ICON_ALT_TEXT: &str = "VRCForge";
const APPROVAL_NOTIFICATION_ACTION_EVENT: &str = "vrcforge-approval-notification-action";
const MAX_APPROVAL_ID_LENGTH: usize = 128;
const MAX_TITLE_LENGTH: usize = 120;
const MAX_BODY_LENGTH: usize = 512;
const MAX_ACTION_LABEL_LENGTH: usize = 32;

#[cfg(windows)]
pub(crate) fn bind_approval_notification_identity() -> bool {
    if register_approval_notification_identity().is_err() {
        return false;
    }
    let app_id = wide_null(APPROVAL_NOTIFICATION_APP_ID);
    unsafe { SetCurrentProcessExplicitAppUserModelID(app_id.as_ptr()) == 0 }
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopApprovalNotificationRequest {
    approval_id: String,
    title: String,
    body: String,
    approve_label: String,
    reject_label: String,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct ApprovalNotificationActionEvent {
    approval_id: String,
    action: &'static str,
}

/// Displays a bounded, display-only approval toast.
///
/// This command never invokes the backend, an approval command, Unity, or a
/// tool. A toast action only emits an event for the existing frontend approval
/// flow to re-check the active session, pending state, and project scope.
#[tauri::command]
pub(crate) fn show_approval_notification(
    app: AppHandle,
    request: DesktopApprovalNotificationRequest,
) -> Result<(), String> {
    #[cfg(windows)]
    {
        validate_notification_request(&request)?;
        if !bind_approval_notification_identity() {
            return Err("Unable to bind the VRCForge notification identity.".to_string());
        }
        let executable = std::env::current_exe()
            .map_err(|_| "Unable to resolve the VRCForge notification icon.".to_string())?;
        let icon_path = approval_notification_icon_path(&executable)
            .filter(|path| path.is_file())
            .ok_or_else(|| "Unable to resolve the VRCForge notification icon.".to_string())?;
        let approval_id = request.approval_id;
        let callback_app = app.clone();
        tauri_winrt_notification::Toast::new(APPROVAL_NOTIFICATION_APP_ID)
            .title(&request.title)
            .text1(&request.body)
            .icon(
                &icon_path,
                tauri_winrt_notification::IconCrop::Square,
                APPROVAL_NOTIFICATION_ICON_ALT_TEXT,
            )
            .add_button(
                &request.approve_label,
                &approval_notification_action("approve", &approval_id),
            )
            .add_button(
                &request.reject_label,
                &approval_notification_action("reject", &approval_id),
            )
            .on_activated(move |activation| {
                let Some(action) = activation
                    .as_deref()
                    .and_then(|value| parse_approval_notification_action(value, &approval_id))
                else {
                    return Ok(());
                };
                let _ = callback_app.emit(
                    APPROVAL_NOTIFICATION_ACTION_EVENT,
                    ApprovalNotificationActionEvent {
                        approval_id: approval_id.clone(),
                        action,
                    },
                );
                focus_main_window(&callback_app);
                Ok(())
            })
            .show()
            .map_err(|_| "Unable to show the approval notification.".to_string())
    }

    #[cfg(not(windows))]
    {
        let _ = app;
        let _ = request;
        Err("Approval notifications are only supported on Windows.".to_string())
    }
}

#[cfg(windows)]
fn register_approval_notification_identity() -> Result<(), ()> {
    // Permission: current-user HKCU only. Lifecycle: the reusable product
    // identity persists until uninstall or a later VRCForge launch refreshes
    // its executable path. Authentication: all values come from this process's
    // own executable and fixed product constants, never from an external caller.
    let executable = std::env::current_exe().map_err(|_| ())?;
    register_approval_notification_identity_for_executable(&executable)
}

#[cfg(windows)]
fn register_approval_notification_identity_for_executable(executable: &Path) -> Result<(), ()> {
    if !executable.is_absolute() {
        return Err(());
    }
    let icon_path = approval_notification_icon_path(executable).ok_or(())?;
    if !icon_path.is_file() {
        return Err(());
    }
    let subkey = wide_null(&format!(
        r"Software\Classes\AppUserModelId\{APPROVAL_NOTIFICATION_APP_ID}"
    ));
    let mut key = std::ptr::null_mut();
    let status = unsafe {
        RegCreateKeyExW(
            HKEY_CURRENT_USER,
            subkey.as_ptr(),
            0,
            std::ptr::null_mut(),
            REG_OPTION_NON_VOLATILE,
            KEY_SET_VALUE,
            std::ptr::null(),
            &mut key,
            std::ptr::null_mut(),
        )
    };
    if status != ERROR_SUCCESS {
        return Err(());
    }

    let icon_text = icon_path.to_string_lossy();
    let display_name = set_registry_string(key, "DisplayName", APPROVAL_NOTIFICATION_DISPLAY_NAME);
    let background = set_registry_string(
        key,
        "IconBackgroundColor",
        APPROVAL_NOTIFICATION_ICON_BACKGROUND_COLOR,
    );
    let icon = set_registry_string(key, "IconUri", icon_text.as_ref());
    unsafe {
        RegCloseKey(key);
    }
    if display_name == ERROR_SUCCESS && background == ERROR_SUCCESS && icon == ERROR_SUCCESS {
        Ok(())
    } else {
        Err(())
    }
}

#[cfg(windows)]
fn approval_notification_icon_path(executable: &Path) -> Option<PathBuf> {
    executable
        .parent()
        .map(|directory| directory.join(APPROVAL_NOTIFICATION_ICON_FILE_NAME))
}

#[cfg(windows)]
fn set_registry_string(
    key: windows_sys::Win32::System::Registry::HKEY,
    name: &str,
    value: &str,
) -> u32 {
    let name = wide_null(name);
    let value = wide_null(value);
    unsafe {
        RegSetValueExW(
            key,
            name.as_ptr(),
            0,
            REG_SZ,
            value.as_ptr().cast(),
            (value.len() * std::mem::size_of::<u16>()) as u32,
        )
    }
}

#[cfg(windows)]
fn wide_null(value: &str) -> Vec<u16> {
    value.encode_utf16().chain(std::iter::once(0)).collect()
}

fn approval_notification_action(action: &str, approval_id: &str) -> String {
    format!("{action}:{approval_id}")
}

fn parse_approval_notification_action(value: &str, approval_id: &str) -> Option<&'static str> {
    ["approve", "reject"]
        .into_iter()
        .find(|action| value == approval_notification_action(action, approval_id))
}

fn validate_notification_request(
    request: &DesktopApprovalNotificationRequest,
) -> Result<(), String> {
    validate_approval_id(&request.approval_id)?;
    validate_display_text(&request.title, MAX_TITLE_LENGTH, "title")?;
    validate_display_text(&request.body, MAX_BODY_LENGTH, "body")?;
    validate_display_text(
        &request.approve_label,
        MAX_ACTION_LABEL_LENGTH,
        "approve label",
    )?;
    validate_display_text(
        &request.reject_label,
        MAX_ACTION_LABEL_LENGTH,
        "reject label",
    )?;
    Ok(())
}

fn validate_approval_id(value: &str) -> Result<(), String> {
    if value.is_empty()
        || value.len() > MAX_APPROVAL_ID_LENGTH
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
    {
        return Err("Invalid approval notification identifier.".to_string());
    }
    Ok(())
}

fn validate_display_text(value: &str, maximum_length: usize, field: &str) -> Result<(), String> {
    let trimmed = value.trim();
    if trimmed.is_empty()
        || trimmed.chars().count() > maximum_length
        || trimmed.chars().any(char::is_control)
    {
        return Err(format!("Invalid approval notification {field}."));
    }
    Ok(())
}

fn focus_main_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.set_focus();
    }
}

#[cfg(test)]
mod tests {
    #[cfg(windows)]
    use super::approval_notification_icon_path;
    use super::{
        approval_notification_action, parse_approval_notification_action,
        validate_notification_request, DesktopApprovalNotificationRequest,
        APPROVAL_NOTIFICATION_APP_ID, APPROVAL_NOTIFICATION_DISPLAY_NAME,
        APPROVAL_NOTIFICATION_ICON_ALT_TEXT, APPROVAL_NOTIFICATION_ICON_BACKGROUND_COLOR,
        APPROVAL_NOTIFICATION_ICON_FILE_NAME, MAX_ACTION_LABEL_LENGTH,
    };
    #[cfg(windows)]
    use std::path::Path;

    fn request() -> DesktopApprovalNotificationRequest {
        DesktopApprovalNotificationRequest {
            approval_id: "approval_123-abc".to_string(),
            title: "Approval required".to_string(),
            body: "Review this pending operation in VRCForge.".to_string(),
            approve_label: "Allow once".to_string(),
            reject_label: "Reject".to_string(),
        }
    }

    #[test]
    fn toast_actions_are_exact_and_limited_to_approve_or_reject() {
        let id = "approval_123-abc";
        assert_eq!(
            approval_notification_action("approve", id),
            "approve:approval_123-abc"
        );
        assert_eq!(
            approval_notification_action("reject", id),
            "reject:approval_123-abc"
        );
        assert_eq!(
            parse_approval_notification_action("approve:approval_123-abc", id),
            Some("approve")
        );
        assert_eq!(
            parse_approval_notification_action("reject:approval_123-abc", id),
            Some("reject")
        );
        assert_eq!(
            parse_approval_notification_action("approve:another-approval", id),
            None
        );
        assert_eq!(
            parse_approval_notification_action("open:approval_123-abc", id),
            None
        );
    }

    #[test]
    fn toast_identity_is_always_vrcforge_owned() {
        assert_eq!(APPROVAL_NOTIFICATION_APP_ID, "app.vrcforge.agentic");
        assert_eq!(APPROVAL_NOTIFICATION_DISPLAY_NAME, "VRCForge");
        assert_eq!(APPROVAL_NOTIFICATION_ICON_BACKGROUND_COLOR, "0");
        assert_eq!(APPROVAL_NOTIFICATION_ICON_FILE_NAME, "VRCForge.png");
        assert_eq!(APPROVAL_NOTIFICATION_ICON_ALT_TEXT, "VRCForge");
    }

    #[cfg(windows)]
    #[test]
    fn toast_identity_uses_the_packaged_icon_beside_the_desktop_executable() {
        assert_eq!(
            approval_notification_icon_path(Path::new(r"C:\\portable\\VRCForge.exe")),
            Some(Path::new(r"C:\\portable\\VRCForge.png").to_path_buf())
        );
    }

    #[test]
    fn toast_request_rejects_invalid_identifiers_and_unbounded_text() {
        assert!(validate_notification_request(&request()).is_ok());

        let mut invalid_id = request();
        invalid_id.approval_id = "approval:123".to_string();
        assert!(validate_notification_request(&invalid_id).is_err());

        let mut control = request();
        control.body = "line one\nline two".to_string();
        assert!(validate_notification_request(&control).is_err());

        let mut too_long = request();
        too_long.approve_label = "x".repeat(MAX_ACTION_LABEL_LENGTH + 1);
        assert!(validate_notification_request(&too_long).is_err());
    }
}

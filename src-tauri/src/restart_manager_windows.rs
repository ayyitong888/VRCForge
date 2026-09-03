//! Windows Restart Manager cooperation for installer-driven upgrades.
//!
//! Permission scope: this hook can only request the current VRCForge process's
//! existing graceful shutdown path; it cannot terminate another process or
//! mutate files. Lifecycle: the subclass is owned by the main window and ends
//! with that process. Authentication: Windows delivers the standard
//! `WM_QUERYENDSESSION`/`WM_ENDSESSION` sequence through the HWND boundary and
//! UIPI; a matching close-app query must precede the terminal notification.

use std::sync::atomic::{AtomicBool, Ordering};

use windows_sys::Win32::{
    Foundation::{HWND, LPARAM, LRESULT, WPARAM},
    UI::{
        Shell::{DefSubclassProc, SetWindowSubclass},
        WindowsAndMessaging::{
            PostMessageW, ENDSESSION_CLOSEAPP, WM_CLOSE, WM_ENDSESSION, WM_QUERYENDSESSION,
        },
    },
};

const RESTART_MANAGER_SUBCLASS_ID: usize = 0x5652_4346;
static CLOSE_APP_QUERY_ACCEPTED: AtomicBool = AtomicBool::new(false);
static RESTART_MANAGER_SHUTDOWN_REQUESTED: AtomicBool = AtomicBool::new(false);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum RestartManagerMessage {
    Other,
    QueryCloseApp,
    EndCloseApp,
}

fn classify_restart_manager_message(
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> RestartManagerMessage {
    let is_close_app = (lparam as u32 & ENDSESSION_CLOSEAPP) != 0;
    match message {
        WM_QUERYENDSESSION if is_close_app => RestartManagerMessage::QueryCloseApp,
        WM_ENDSESSION if wparam != 0 && is_close_app => RestartManagerMessage::EndCloseApp,
        _ => RestartManagerMessage::Other,
    }
}

pub(crate) fn install_restart_manager_window_hook(
    window: &tauri::WebviewWindow,
) -> Result<(), String> {
    let hwnd = window
        .hwnd()
        .map_err(|error| format!("main window handle unavailable: {error}"))?;
    let installed = unsafe {
        SetWindowSubclass(
            hwnd.0 as HWND,
            Some(restart_manager_window_proc),
            RESTART_MANAGER_SUBCLASS_ID,
            0,
        )
    };
    if installed == 0 {
        return Err("Windows Restart Manager window hook could not be installed.".to_string());
    }
    Ok(())
}

pub(crate) fn take_restart_manager_shutdown_request() -> bool {
    RESTART_MANAGER_SHUTDOWN_REQUESTED.swap(false, Ordering::AcqRel)
}

unsafe extern "system" fn restart_manager_window_proc(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
    _subclass_id: usize,
    _reference_data: usize,
) -> LRESULT {
    match classify_restart_manager_message(message, wparam, lparam) {
        RestartManagerMessage::QueryCloseApp => {
            CLOSE_APP_QUERY_ACCEPTED.store(true, Ordering::Release);
            1
        }
        RestartManagerMessage::EndCloseApp => {
            if CLOSE_APP_QUERY_ACCEPTED.swap(false, Ordering::AcqRel) {
                RESTART_MANAGER_SHUTDOWN_REQUESTED.store(true, Ordering::Release);
                let result = DefSubclassProc(hwnd, message, wparam, lparam);
                let _ = PostMessageW(hwnd, WM_CLOSE, 0, 0);
                result
            } else {
                DefSubclassProc(hwnd, message, wparam, lparam)
            }
        }
        RestartManagerMessage::Other => DefSubclassProc(hwnd, message, wparam, lparam),
    }
}

#[cfg(test)]
mod tests {
    use super::{classify_restart_manager_message, RestartManagerMessage};
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        ENDSESSION_CLOSEAPP, WM_CLOSE, WM_ENDSESSION, WM_QUERYENDSESSION,
    };

    #[test]
    fn accepts_only_the_restart_manager_close_app_sequence() {
        assert_eq!(
            classify_restart_manager_message(WM_QUERYENDSESSION, 0, ENDSESSION_CLOSEAPP as isize,),
            RestartManagerMessage::QueryCloseApp
        );
        assert_eq!(
            classify_restart_manager_message(WM_ENDSESSION, 1, ENDSESSION_CLOSEAPP as isize),
            RestartManagerMessage::EndCloseApp
        );
        assert_eq!(
            classify_restart_manager_message(WM_ENDSESSION, 0, ENDSESSION_CLOSEAPP as isize),
            RestartManagerMessage::Other
        );
        assert_eq!(
            classify_restart_manager_message(WM_CLOSE, 0, 0),
            RestartManagerMessage::Other
        );
    }
}

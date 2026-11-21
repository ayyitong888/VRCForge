use tauri::WebviewWindow;
use webview2_com::Microsoft::Web::WebView2::Win32::ICoreWebView2Settings3;
use windows_core::Interface;
use windows_sys::Win32::{
    Foundation::{HWND, LPARAM, LRESULT, WPARAM},
    UI::{
        Shell::{DefSubclassProc, RemoveWindowSubclass, SetWindowSubclass},
        WindowsAndMessaging::{WM_EXITSIZEMOVE, WM_NCDESTROY, WM_SIZING},
    },
};

pub fn configure(window: &WebviewWindow) -> tauri::Result<()> {
    if let Ok(hwnd) = window.hwnd() {
        let state = Box::into_raw(Box::new(ImeResize {
            window: window.clone(),
            sizing: false,
        }));
        // The main HWND owns this in-process hook and its state until destruction.
        // It only publishes fixed DOM events to that window's WebView.
        if unsafe {
            SetWindowSubclass(
                hwnd.0 as HWND,
                Some(ime_resize_proc),
                0x494d45,
                state as usize,
            )
        } == 0
        {
            unsafe { drop(Box::from_raw(state)) };
        }
    }
    window.with_webview(|webview| {
        let result = (|| unsafe {
            let settings = webview.controller().CoreWebView2()?.Settings()?;
            // This preserves editing shortcuts such as Ctrl+A, Ctrl+Z and Ctrl+V.
            settings
                .cast::<ICoreWebView2Settings3>()?
                .SetAreBrowserAcceleratorKeysEnabled(false)?;
            settings.SetAreDefaultContextMenusEnabled(false)
        })();
        if let Err(error) = result {
            eprintln!("Could not configure WebView2 browser controls: {error}");
        }
    })
}

struct ImeResize {
    window: WebviewWindow,
    sizing: bool,
}

unsafe extern "system" fn ime_resize_proc(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
    id: usize,
    data: usize,
) -> LRESULT {
    let state = data as *mut ImeResize;
    let script = match message {
        WM_SIZING if !(*state).sizing => {
            (*state).sizing = true;
            Some("window.dispatchEvent(new Event('vrcforge-native-resize-start'))")
        }
        WM_EXITSIZEMOVE if (*state).sizing => {
            (*state).sizing = false;
            Some("window.dispatchEvent(new Event('vrcforge-native-resize-end'))")
        }
        WM_NCDESTROY => {
            RemoveWindowSubclass(hwnd, Some(ime_resize_proc), id);
            drop(Box::from_raw(state));
            None
        }
        _ => None,
    };
    if let Some(script) = script {
        let _ = (*state).window.eval(script);
    }
    DefSubclassProc(hwnd, message, wparam, lparam)
}

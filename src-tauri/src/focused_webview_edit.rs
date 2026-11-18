use serde::Deserialize;
use tauri::WebviewWindow;

#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EditAction {
    Cut,
    Copy,
    Paste,
}

#[tauri::command]
pub async fn edit_focused_webview(window: WebviewWindow, action: EditAction) -> Result<(), String> {
    #[cfg(windows)]
    {
        use webview2_com::{CallDevToolsProtocolMethodCompletedHandler, CoTaskMemPWSTR};
        use windows_sys::Win32::UI::WindowsAndMessaging::GetForegroundWindow;

        if window.label() != "main" {
            return Err("Clipboard editing requires the focused main window".into());
        }
        let main_hwnd = window.hwnd().map_err(|err| err.to_string())?.0 as usize;

        let (command, code, key, virtual_key) = match action {
            EditAction::Cut => ("Cut", "KeyX", "x", 88),
            EditAction::Copy => ("Copy", "KeyC", "c", 67),
            EditAction::Paste => ("Paste", "KeyV", "v", 86),
        };
        let parameters = serde_json::json!({
            "type": "rawKeyDown",
            "modifiers": 2,
            "code": code,
            "key": key,
            "windowsVirtualKeyCode": virtual_key,
            "commands": [command],
        })
        .to_string();

        // Run the built-in edit command inside this WebView. It delivers the same
        // clipboard events and formats as a keyboard shortcut.
        let (sender, mut result) = tauri::async_runtime::channel(1);
        window
            .with_webview(move |webview| {
                // A focused child WebView can leave Tao's top-level focus flag unset.
                // Check the actual foreground window at dispatch time.
                if unsafe { GetForegroundWindow() } as usize != main_hwnd {
                    let _ = sender.try_send(Err(
                        "Clipboard editing requires the focused main window".into(),
                    ));
                    return;
                }
                let callback_sender = sender.clone();
                let callback = CallDevToolsProtocolMethodCompletedHandler::create(Box::new(
                    move |status, _| {
                        let _ = callback_sender.try_send(status.map_err(|err| err.to_string()));
                        Ok(())
                    },
                ));
                let method = CoTaskMemPWSTR::from("Input.dispatchKeyEvent");
                let parameters = CoTaskMemPWSTR::from(parameters.as_str());
                let started = unsafe {
                    webview.controller().CoreWebView2().and_then(|core| {
                        core.CallDevToolsProtocolMethod(
                            *method.as_ref().as_pcwstr(),
                            *parameters.as_ref().as_pcwstr(),
                            &callback,
                        )
                    })
                };
                if let Err(err) = started {
                    let _ = sender.try_send(Err(err.to_string()));
                }
            })
            .map_err(|err| err.to_string())?;
        result
            .recv()
            .await
            .ok_or_else(|| "WebView closed before clipboard editing completed".to_string())?
    }

    #[cfg(not(windows))]
    {
        let _ = (window, action);
        Err("Focused WebView editing requires WebView2".into())
    }
}

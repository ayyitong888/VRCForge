use tauri::WebviewWindow;
use webview2_com::Microsoft::Web::WebView2::Win32::ICoreWebView2Settings3;
use windows_core::Interface;

pub fn configure(window: &WebviewWindow) -> tauri::Result<()> {
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

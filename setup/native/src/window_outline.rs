use windows_sys::Win32::{
    Foundation::{POINT, RECT},
    Graphics::Gdi::{ClientToScreen, CreateRoundRectRgn, DeleteObject, SetWindowRgn},
    UI::WindowsAndMessaging::{GetClientRect, GetWindowRect},
};

// Match the homepage's --site-hero-radius, expressed in logical pixels.
const CORNER_RADIUS: f64 = 28.0;

pub fn apply(window: &tauri::Window) -> Result<(), String> {
    let hwnd = window.hwnd().map_err(|error| error.to_string())?.0 as _;
    let mut client = RECT { left: 0, top: 0, right: 0, bottom: 0 };
    let mut outer = RECT { left: 0, top: 0, right: 0, bottom: 0 };
    let mut origin = POINT { x: 0, y: 0 };
    unsafe {
        if GetClientRect(hwnd, &mut client) == 0
            || ClientToScreen(hwnd, &mut origin) == 0
            || GetWindowRect(hwnd, &mut outer) == 0
        {
            return Err(std::io::Error::last_os_error().to_string());
        }
        // Region coordinates are relative to the outer window. Clipping the
        // client rectangle also excludes any non-client Windows frame margins.
        let left = origin.x - outer.left;
        let top = origin.y - outer.top;
        let diameter = (CORNER_RADIUS * 2.0 * window.scale_factor().map_err(|error| error.to_string())?).round() as i32;
        let region = CreateRoundRectRgn(left, top, left + client.right, top + client.bottom, diameter, diameter);
        if region.is_null() {
            return Err(std::io::Error::last_os_error().to_string());
        }
        if SetWindowRgn(hwnd, region, 1) == 0 {
            let error = std::io::Error::last_os_error();
            DeleteObject(region);
            return Err(error.to_string());
        }
        // On success Windows owns HRGN and releases it when replaced/destroyed.
    }
    Ok(())
}

use serde_json::json;
use std::{
    collections::HashSet,
    env, fs,
    path::{Path, PathBuf},
};
use windows_capture::{
    capture::{Context, GraphicsCaptureApiHandler},
    encoder::ImageFormat,
    frame::Frame,
    graphics_capture_api::InternalCaptureControl,
    settings::{
        ColorFormat, CursorCaptureSettings, DirtyRegionSettings, DrawBorderSettings,
        MinimumUpdateIntervalSettings, SecondaryWindowSettings, Settings,
    },
    window::Window,
};

#[derive(Clone)]
struct CaptureFlags {
    output: PathBuf,
    status: PathBuf,
}

struct OneShotCapture {
    flags: CaptureFlags,
    wrote_frame: bool,
}

impl GraphicsCaptureApiHandler for OneShotCapture {
    type Flags = CaptureFlags;
    type Error = Box<dyn std::error::Error + Send + Sync>;

    fn new(context: Context<Self::Flags>) -> Result<Self, Self::Error> {
        Ok(Self {
            flags: context.flags,
            wrote_frame: false,
        })
    }

    fn on_frame_arrived(
        &mut self,
        frame: &mut Frame,
        capture_control: InternalCaptureControl,
    ) -> Result<(), Self::Error> {
        let width = frame.width();
        let height = frame.height();
        if width == 0 || height == 0 || u64::from(width) * u64::from(height) > 80_000_000 {
            return Err("captured frame dimensions are invalid or exceed the limit".into());
        }
        let mut frame_buffer = frame.buffer()?;
        let mut compact = Vec::new();
        let sample_color_count = {
            let bytes = frame_buffer.as_nopadding_buffer(&mut compact);
            let pixel_count = bytes.len() / 4;
            let pixel_step = (pixel_count / 4096).max(1);
            let mut colors = HashSet::new();
            for pixel in bytes.chunks_exact(4).step_by(pixel_step) {
                colors.insert([pixel[0], pixel[1], pixel[2]]);
            }
            colors.len()
        };
        frame_buffer.save_as_image(&self.flags.output, ImageFormat::Png)?;
        write_status(
            &self.flags.status,
            json!({
                "ok": true,
                "captureBackend": "windows_graphics_capture",
                "occlusionSafe": true,
                "width": width,
                "height": height,
                "sampleColorCount": sample_color_count,
                "frameWarning": if sample_color_count <= 1 { "uniform_frame" } else { "" },
            }),
        )?;
        self.wrote_frame = true;
        capture_control.stop();
        Ok(())
    }

    fn on_closed(&mut self) -> Result<(), Self::Error> {
        if !self.wrote_frame {
            write_status(
                &self.flags.status,
                json!({"ok": false, "error": "The target window closed before a frame was captured."}),
            )?;
        }
        Ok(())
    }
}

fn write_status(
    path: &Path,
    payload: serde_json::Value,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(path, serde_json::to_vec(&payload)?)?;
    Ok(())
}

fn argument_value(args: &[String], name: &str) -> Option<String> {
    args.iter()
        .position(|argument| argument == name)
        .and_then(|index| args.get(index + 1))
        .cloned()
}

pub(crate) fn try_run_from_args() -> Option<i32> {
    let args: Vec<String> = env::args().collect();
    let handle = argument_value(&args, "--vrcforge-capture-window")?;
    let output = argument_value(&args, "--output").map(PathBuf::from);
    let status = argument_value(&args, "--status").map(PathBuf::from);
    let (Some(output), Some(status)) = (output, status) else {
        return Some(2);
    };
    let result = (|| -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        let raw_handle: isize = handle.parse()?;
        if raw_handle <= 0 {
            return Err("window handle must be positive".into());
        }
        if output.extension().and_then(|value| value.to_str()) != Some("png") {
            return Err("capture output must use the .png extension".into());
        }
        if let Some(parent) = output.parent() {
            fs::create_dir_all(parent)?;
        }
        let window = Window::from_raw_hwnd(raw_handle as *mut std::ffi::c_void);
        let settings = Settings::new(
            window,
            CursorCaptureSettings::Default,
            DrawBorderSettings::Default,
            SecondaryWindowSettings::Default,
            MinimumUpdateIntervalSettings::Default,
            DirtyRegionSettings::Default,
            ColorFormat::Bgra8,
            CaptureFlags {
                output: output.clone(),
                status: status.clone(),
            },
        );
        OneShotCapture::start(settings)?;
        if !output.is_file() || !status.is_file() {
            return Err("capture completed without producing the bounded output files".into());
        }
        Ok(())
    })();
    match result {
        Ok(()) => Some(0),
        Err(error) => {
            let _ = fs::remove_file(&output);
            let _ = write_status(&status, json!({"ok": false, "error": error.to_string()}));
            Some(1)
        }
    }
}

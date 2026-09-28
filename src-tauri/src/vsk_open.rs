use std::collections::VecDeque;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

pub const EVENT_NAME: &str = "vrcforge:vsk-open";
const MAX_PENDING_PATHS: usize = 32;

#[derive(Default)]
pub struct PendingVskPaths(Mutex<VecDeque<String>>);

impl PendingVskPaths {
    pub fn enqueue_args<I, S>(&self, args: I, cwd: &Path)
    where
        I: IntoIterator<Item = S>,
        S: AsRef<str>,
    {
        let mut pending = self
            .0
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        for arg in args {
            if pending.len() >= MAX_PENDING_PATHS {
                break;
            }
            if let Ok(path) = validate_vsk_path(arg.as_ref(), cwd) {
                if let Some(path) = path.to_str() {
                    pending.push_back(path.to_owned());
                }
            }
        }
    }

    pub fn take(&self) -> Vec<String> {
        self.0
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .drain(..)
            .collect()
    }
}

pub fn validate_vsk_path(value: &str, cwd: &Path) -> Result<PathBuf, &'static str> {
    if value.is_empty()
        || value.starts_with('-')
        || value.starts_with('/')
        || value.starts_with('\\')
        || value.contains('\0')
        || value.starts_with("//")
        || value.starts_with("\\\\")
    {
        return Err("not a local VSK path");
    }

    let input = Path::new(value);
    if !input
        .extension()
        .and_then(|extension| extension.to_str())
        .is_some_and(|extension| extension.eq_ignore_ascii_case("vsk"))
    {
        return Err("not a VSK file");
    }
    #[cfg(windows)]
    if value.starts_with("\\\\?\\") || value.starts_with("\\\\.\\") {
        return Err("device paths are not accepted");
    }

    let joined = if input.is_absolute() {
        input.to_path_buf()
    } else {
        cwd.join(input)
    };
    let leaf = std::fs::symlink_metadata(&joined).map_err(|_| "VSK file is unavailable")?;
    if !leaf.file_type().is_file() {
        return Err("VSK path is not a regular file");
    }
    let canonical = joined
        .canonicalize()
        .map_err(|_| "VSK file is unavailable")?;
    let metadata = std::fs::metadata(&canonical).map_err(|_| "VSK file is unavailable")?;
    if !metadata.is_file() {
        return Err("VSK path is not a regular file");
    }

    #[cfg(windows)]
    if is_remote_windows_path(&canonical) {
        return Err("network paths are not accepted");
    }
    #[cfg(not(windows))]
    if canonical.to_str().is_some_and(|path| {
        path.starts_with("//") || path.starts_with("\\\\") || path.starts_with("/dev/")
    }) {
        return Err("network or device paths are not accepted");
    }

    canonical
        .to_str()
        .map(PathBuf::from)
        .ok_or("path is not Unicode")
}

#[cfg(windows)]
fn is_remote_windows_path(path: &Path) -> bool {
    use windows_sys::Win32::Storage::FileSystem::GetDriveTypeW;

    let Some(path) = path.to_str() else {
        return true;
    };
    if path.starts_with("\\\\?\\UNC\\") || path.starts_with("\\\\.\\") {
        return true;
    }
    let drive_path = path.strip_prefix("\\\\?\\").unwrap_or(path);
    if drive_path.starts_with("\\\\") {
        return true;
    }
    let Some(drive) = drive_path
        .get(..2)
        .filter(|prefix| prefix.as_bytes().get(1) == Some(&b':'))
    else {
        return true;
    };
    let mut wide: Vec<u16> = drive.encode_utf16().collect();
    wide.push(b'\\' as u16);
    wide.push(0);
    const DRIVE_REMOTE: u32 = 4;
    unsafe { GetDriveTypeW(wide.as_ptr()) == DRIVE_REMOTE }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn test_dir() -> PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let path = std::env::temp_dir().join(format!("vsk-open-{nonce}"));
        fs::create_dir_all(&path).unwrap();
        path
    }

    #[test]
    fn accepts_existing_vsk_case_insensitively_and_resolves_relative_to_cwd() {
        let root = test_dir();
        let file = root.join("Guide.VSK");
        fs::write(&file, b"test").unwrap();
        assert_eq!(
            validate_vsk_path("Guide.VSK", &root).unwrap(),
            file.canonicalize().unwrap()
        );
        assert_eq!(
            validate_vsk_path(file.to_str().unwrap(), &root).unwrap(),
            file.canonicalize().unwrap()
        );
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn rejects_options_network_device_missing_and_non_vsk_paths() {
        let root = test_dir();
        for value in [
            "",
            "--version.vsk",
            "//server/share/a.vsk",
            "\\\\server\\share\\a.vsk",
            "\\\\.\\pipe\\x.vsk",
            "missing.vsk",
            "readme.txt",
        ] {
            assert!(
                validate_vsk_path(value, &root).is_err(),
                "accepted {value:?}"
            );
        }
        let directory = root.join("folder.vsk");
        fs::create_dir(&directory).unwrap();
        assert!(validate_vsk_path(directory.to_str().unwrap(), &root).is_err());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn queue_is_bounded_and_take_consumes_all_pending_paths() {
        let root = test_dir();
        let mut args = Vec::new();
        for index in 0..40 {
            let path = root.join(format!("{index}.vsk"));
            fs::write(&path, b"test").unwrap();
            args.push(path.to_string_lossy().into_owned());
        }
        let pending = PendingVskPaths::default();
        pending.enqueue_args(args, &root);
        let taken = pending.take();
        assert_eq!(taken.len(), MAX_PENDING_PATHS);
        assert!(pending.take().is_empty());
        fs::remove_dir_all(root).unwrap();
    }
}

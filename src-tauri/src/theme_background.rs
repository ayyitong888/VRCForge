use crate::backend::user_data_dir;
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::{self, Read},
    path::{Path, PathBuf},
    sync::Mutex,
};

const THEME_BACKGROUND_DIRECTORY: &str = "theme";
const THEME_BACKGROUND_PREFIX: &str = "background-";
const SUPPORTED_EXTENSIONS: [&str; 5] = ["png", "jpg", "jpeg", "webp", "gif"];
static THEME_BACKGROUND_LOCK: Mutex<()> = Mutex::new(());

/// Opens a user-owned native picker and copies the selected image into the
/// VRCForge-owned theme directory. The local Tauri WebView is the only caller;
/// read access lasts for the picker operation and the managed copy lives until
/// it is replaced, cleared, or the App data is removed.
#[tauri::command]
pub(crate) fn pick_theme_background() -> Result<Option<String>, String> {
    #[cfg(windows)]
    {
        let selected = rfd::FileDialog::new()
            .set_title("Choose a VRCForge background image")
            .add_filter("Images", &SUPPORTED_EXTENSIONS)
            .pick_file();
        let _guard = theme_background_lock()?;
        selected
            .map(|path| persist_theme_background_file(&path))
            .transpose()
            .map(|path| path.map(|value| value.display().to_string()))
    }

    #[cfg(not(windows))]
    {
        Err("Background image selection is only available on Windows desktop builds.".to_string())
    }
}

/// One-time compatibility path for the 1.7.1 localStorage representation.
/// New selections never cross IPC as image bytes and never use Base64.
#[tauri::command]
pub(crate) fn import_legacy_theme_background(
    bytes: Vec<u8>,
    extension: String,
) -> Result<String, String> {
    let _guard = theme_background_lock()?;
    let theme_dir = theme_background_directory()?;
    if let Some(existing) = first_managed_background(&theme_dir)? {
        return Ok(existing.display().to_string());
    }
    persist_theme_background_bytes(&bytes, &extension).map(|path| path.display().to_string())
}

/// Removes only VRCForge-owned managed backgrounds from the App data theme
/// directory. It accepts no caller-provided path and cannot delete user files.
#[tauri::command]
pub(crate) fn clear_theme_background() -> Result<(), String> {
    let _guard = theme_background_lock()?;
    let theme_dir = theme_background_directory()?;
    remove_managed_backgrounds(&theme_dir, None)
}

fn theme_background_lock() -> Result<std::sync::MutexGuard<'static, ()>, String> {
    THEME_BACKGROUND_LOCK
        .lock()
        .map_err(|_| "The theme background manager is unavailable.".to_string())
}

fn persist_theme_background_file(source: &Path) -> Result<PathBuf, String> {
    let user_data = user_data_dir()?;
    persist_theme_background_file_into(source, &user_data)
}

fn persist_theme_background_file_into(source: &Path, user_data: &Path) -> Result<PathBuf, String> {
    if !source.is_file() {
        return Err("The selected background image is not a file.".to_string());
    }
    let extension = normalized_extension(source)?;
    validate_image_signature_from_file(source, &extension)?;

    let mut reader = fs::File::open(source)
        .map_err(|error| format!("Unable to read the selected background image: {error}"))?;
    let mut hasher = Sha256::new();
    io::copy(&mut reader, &mut DigestWriter(&mut hasher))
        .map_err(|error| format!("Unable to inspect the selected background image: {error}"))?;
    let digest = format!("{:x}", hasher.finalize());
    let theme_dir = theme_background_directory_at(user_data)?;
    let destination = theme_dir.join(format!(
        "{THEME_BACKGROUND_PREFIX}{}.{}",
        &digest[..16],
        canonical_extension(&extension)
    ));

    if !destination.is_file() {
        let pending = theme_dir.join(format!(
            ".pending-{}-{}.{}",
            std::process::id(),
            &digest[..16],
            canonical_extension(&extension)
        ));
        let copy_result =
            fs::copy(source, &pending).and_then(|_| fs::rename(&pending, &destination));
        if let Err(error) = copy_result {
            let _ = fs::remove_file(&pending);
            return Err(format!("Unable to save the background image: {error}"));
        }
    }

    remove_managed_backgrounds(&theme_dir, Some(&destination))?;
    Ok(destination)
}

fn persist_theme_background_bytes(bytes: &[u8], extension: &str) -> Result<PathBuf, String> {
    let user_data = user_data_dir()?;
    persist_theme_background_bytes_into(bytes, extension, &user_data)
}

fn persist_theme_background_bytes_into(
    bytes: &[u8],
    extension: &str,
    user_data: &Path,
) -> Result<PathBuf, String> {
    let extension = extension
        .trim()
        .trim_start_matches('.')
        .to_ascii_lowercase();
    if !SUPPORTED_EXTENSIONS.contains(&extension.as_str()) {
        return Err("The previous background image format is not supported.".to_string());
    }
    validate_image_signature(bytes, &extension)?;
    let digest = format!("{:x}", Sha256::digest(bytes));
    let theme_dir = theme_background_directory_at(user_data)?;
    let destination = theme_dir.join(format!(
        "{THEME_BACKGROUND_PREFIX}{}.{}",
        &digest[..16],
        canonical_extension(&extension)
    ));
    if !destination.is_file() {
        let pending = theme_dir.join(format!(
            ".pending-{}-{}.{}",
            std::process::id(),
            &digest[..16],
            canonical_extension(&extension)
        ));
        let write_result =
            fs::write(&pending, bytes).and_then(|_| fs::rename(&pending, &destination));
        if let Err(error) = write_result {
            let _ = fs::remove_file(&pending);
            return Err(format!(
                "Unable to migrate the previous background image: {error}"
            ));
        }
    }
    remove_managed_backgrounds(&theme_dir, Some(&destination))?;
    Ok(destination)
}

fn theme_background_directory() -> Result<PathBuf, String> {
    let user_data = user_data_dir()?;
    theme_background_directory_at(&user_data)
}

fn theme_background_directory_at(user_data: &Path) -> Result<PathBuf, String> {
    let directory = user_data.join(THEME_BACKGROUND_DIRECTORY);
    fs::create_dir_all(&directory)
        .map_err(|error| format!("Unable to prepare the theme background directory: {error}"))?;
    Ok(directory)
}

fn normalized_extension(path: &Path) -> Result<String, String> {
    let extension = path
        .extension()
        .and_then(|value| value.to_str())
        .map(str::to_ascii_lowercase)
        .ok_or_else(|| "The selected background image has no supported extension.".to_string())?;
    if SUPPORTED_EXTENSIONS.contains(&extension.as_str()) {
        Ok(extension)
    } else {
        Err("Choose a PNG, JPEG, WebP, or GIF image.".to_string())
    }
}

fn canonical_extension(extension: &str) -> &str {
    if extension == "jpeg" {
        "jpg"
    } else {
        extension
    }
}

fn validate_image_signature_from_file(path: &Path, extension: &str) -> Result<(), String> {
    let mut file = fs::File::open(path)
        .map_err(|error| format!("Unable to read the selected background image: {error}"))?;
    let mut header = [0_u8; 12];
    let read = file
        .read(&mut header)
        .map_err(|error| format!("Unable to inspect the selected background image: {error}"))?;
    validate_image_signature(&header[..read], extension)
}

fn validate_image_signature(bytes: &[u8], extension: &str) -> Result<(), String> {
    let valid = match canonical_extension(extension) {
        "png" => bytes.starts_with(b"\x89PNG\r\n\x1a\n"),
        "jpg" => bytes.starts_with(&[0xff, 0xd8, 0xff]),
        "gif" => bytes.starts_with(b"GIF87a") || bytes.starts_with(b"GIF89a"),
        "webp" => bytes.len() >= 12 && bytes.starts_with(b"RIFF") && &bytes[8..12] == b"WEBP",
        _ => false,
    };
    if valid {
        Ok(())
    } else {
        Err("The selected file content does not match its image format.".to_string())
    }
}

fn remove_managed_backgrounds(directory: &Path, keep: Option<&Path>) -> Result<(), String> {
    let entries = match fs::read_dir(directory) {
        Ok(entries) => entries,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(()),
        Err(error) => {
            return Err(format!(
                "Unable to inspect saved theme backgrounds: {error}"
            ))
        }
    };
    for entry in entries {
        let entry = entry
            .map_err(|error| format!("Unable to inspect a saved theme background: {error}"))?;
        let path = entry.path();
        if keep.is_some_and(|current| current == path) || !is_managed_background(&path) {
            continue;
        }
        fs::remove_file(&path)
            .map_err(|error| format!("Unable to remove an old theme background: {error}"))?;
    }
    Ok(())
}

fn first_managed_background(directory: &Path) -> Result<Option<PathBuf>, String> {
    let entries = match fs::read_dir(directory) {
        Ok(entries) => entries,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(error) => {
            return Err(format!(
                "Unable to inspect saved theme backgrounds: {error}"
            ))
        }
    };
    for entry in entries {
        let path = entry
            .map_err(|error| format!("Unable to inspect a saved theme background: {error}"))?
            .path();
        if is_managed_background(&path) {
            return Ok(Some(path));
        }
    }
    Ok(None)
}

fn is_managed_background(path: &Path) -> bool {
    path.is_file()
        && path
            .file_name()
            .and_then(|value| value.to_str())
            .is_some_and(is_managed_background_name)
}

fn is_managed_background_name(name: &str) -> bool {
    name.starts_with(THEME_BACKGROUND_PREFIX)
        && Path::new(name)
            .extension()
            .and_then(|value| value.to_str())
            .map(str::to_ascii_lowercase)
            .is_some_and(|extension| SUPPORTED_EXTENSIONS.contains(&extension.as_str()))
}

struct DigestWriter<'a>(&'a mut Sha256);

impl io::Write for DigestWriter<'_> {
    fn write(&mut self, buffer: &[u8]) -> io::Result<usize> {
        self.0.update(buffer);
        Ok(buffer.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    #[test]
    fn validates_supported_image_signatures() {
        assert!(validate_image_signature(b"\x89PNG\r\n\x1a\nrest", "png").is_ok());
        assert!(validate_image_signature(b"\xff\xd8\xffrest", "jpeg").is_ok());
        assert!(validate_image_signature(b"GIF89arest", "gif").is_ok());
        assert!(validate_image_signature(b"RIFF0000WEBPrest", "webp").is_ok());
        assert!(validate_image_signature(b"not an image", "png").is_err());
    }

    #[test]
    fn recognizes_only_owned_background_names() {
        assert!(is_managed_background_name(
            "background-0123456789abcdef.png"
        ));
        assert!(!is_managed_background_name("holiday.png"));
        assert!(!is_managed_background_name(
            "background-0123456789abcdef.txt"
        ));
    }

    #[test]
    fn persists_large_background_then_replaces_and_clears_owned_file() {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let root = std::env::temp_dir().join(format!(
            "vrcforge-theme-background-{}-{nonce}",
            std::process::id()
        ));
        fs::create_dir_all(&root).expect("temp root");

        let first_source = root.join("large.png");
        let mut first_bytes = b"\x89PNG\r\n\x1a\n".to_vec();
        first_bytes.resize(3 * 1024 * 1024, 0);
        fs::write(&first_source, &first_bytes).expect("large source");
        let first = persist_theme_background_file_into(&first_source, &root).expect("first");
        assert!(first.is_file());
        assert_eq!(
            fs::metadata(&first).expect("first metadata").len(),
            3 * 1024 * 1024
        );

        let second_source = root.join("next.gif");
        fs::write(&second_source, b"GIF89a-next").expect("second source");
        let second = persist_theme_background_file_into(&second_source, &root).expect("second");
        assert!(second.is_file());
        assert!(!first.exists());

        let theme_dir = theme_background_directory_at(&root).expect("theme dir");
        let user_named_file = theme_dir.join("holiday.png");
        fs::write(&user_named_file, b"\x89PNG\r\n\x1a\n-user-owned")
            .expect("user-named background");
        remove_managed_backgrounds(&theme_dir, None).expect("clear");
        assert!(!second.exists());
        assert!(user_named_file.exists());
        fs::remove_dir_all(&root).expect("remove temp root");
    }
}

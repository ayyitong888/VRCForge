//! A staged, same-volume replacement. The old installation and user data remain
//! recoverable until files, shortcuts and the existing uninstall entry commit.
use crate::{models::{InstallRequest, SetupProgress, SetupResult, UninstallRequest}, platform};
use std::{fs::{self, File, OpenOptions}, io::{Read, Seek, SeekFrom, Write}, path::{Path, PathBuf}, time::{SystemTime, UNIX_EPOCH}};
use tauri::Emitter;
#[path = "file_transaction.rs"]
mod file_transaction;
use file_transaction::{same_path, relative_under, remove_directory, activate_directory, restore_program_directory, remove_program_files_preserving_data};

pub use crate::payload::{inspect_payload, PayloadInfo};

pub fn setup_log_path(_user: &platform::UserContext) -> PathBuf {
    std::env::temp_dir().join(format!("VRCForge-setup-{}.log", std::process::id()))
}

fn log(user: &platform::UserContext, message: &str) {
    if let Ok(mut output) = OpenOptions::new().create(true).append(true).open(setup_log_path(user)) {
        let _ = writeln!(output, "{message}");
    }
}

fn progress(app: &tauri::AppHandle, user: &platform::UserContext, stage: &str, percent: u8) {
    log(user, &format!("{stage}: {percent}%"));
    let _ = app.emit("setup-progress", SetupProgress {stage: stage.into(), percent});
}

fn unique_sibling(path: &Path, purpose: &str) -> Result<PathBuf, String> {
    let parent = path.parent().filter(|p| !p.as_os_str().is_empty()).ok_or("Choose an application folder, not a disk root.")?;
    let name = path.file_name().ok_or("The installation folder has no name.")?.to_string_lossy();
    let stamp = SystemTime::now().duration_since(UNIX_EPOCH).map_err(|e| e.to_string())?.as_nanos();
    Ok(parent.join(format!(".{name}-{purpose}-{}-{stamp}", std::process::id())))
}

fn require_scoped_directory(user: &platform::UserContext, dir: &Path) -> Result<(), String> {
    if !dir.is_absolute() || dir.parent().is_none() || dir.file_name().is_none() {
        return Err("Choose an absolute folder, not a disk root.".into());
    }
    let absolute = std::path::absolute(dir).map_err(|e| format!("Cannot resolve folder {}: {e}", dir.display()))?;
    let dir = absolute.as_path();
    for protected in [&user.profile, &user.local_app_data, &user.desktop, &user.start_menu] {
        if same_path(dir, protected) {return Err(format!("{} is a Windows user folder, not a VRCForge-only folder.", dir.display()));}
    }
    for name in ["ProgramFiles", "ProgramFiles(x86)", "ProgramData", "SystemRoot"] {
        if std::env::var_os(name).is_some_and(|root| same_path(dir, Path::new(&root))) {
            return Err(format!("{} is a shared Windows folder, not a VRCForge-only folder.", dir.display()));
        }
    }
    if dir.ancestors().any(|parent| parent.join("Assets").is_dir() && parent.join("ProjectSettings").is_dir()) {
        return Err(format!("{} belongs to a Unity project. Choose a separate VRCForge program folder.", dir.display()));
    }
    Ok(())
}

pub fn validate_install_directory(user: &platform::UserContext, dir: &Path, registered: Option<&platform::InstalledApp>) -> Result<(), String> {
    require_scoped_directory(user, dir)?;
    let data = platform::data_dir(user)?;
    if relative_under(dir, &data).is_some() {
        return Err("Choose a program folder separate from VRCForge personal data.".into());
    }
    if dir.exists()
        && fs::read_dir(dir).map_err(|e| format!("Cannot read {}: {e}", dir.display()))?.next().is_some()
        && !registered.is_some_and(|old| same_path(&old.install_dir, dir)) {
        return Err(format!("{} is not empty and is not a registered VRCForge installation. Choose another folder.", dir.display()));
    }
    Ok(())
}

fn extract_payload(setup: &Path, info: &PayloadInfo, destination: &Path, mut completed: impl FnMut(u64)) -> Result<(), String> {
    // The HTTPS response and decoder are scoped to this extraction. The fixed
    // versioned URL comes from setup's packaged metadata, never a UI argument.
    let source: Box<dyn Read> = if let Some(url) = info.download_url.as_ref() {
        let client = reqwest::blocking::Client::builder().timeout(std::time::Duration::from_secs(1800)).build().map_err(|e| e.to_string())?;
        let response = client.get(url).send().and_then(|response| response.error_for_status())
            .map_err(|e| format!("Cannot download the application payload from {url}: {e}"))?;
        Box::new(response)
    } else {
        let mut file = File::open(setup).map_err(|e| format!("Cannot open setup: {e}"))?;
        file.seek(SeekFrom::Start(info.executable_bytes)).map_err(|e| e.to_string())?;
        Box::new(file.take(info.payload_bytes))
    };
    let decoder = zstd::stream::read::Decoder::new(source).map_err(|e| format!("Cannot decode the application payload: {e}"))?;
    let mut archive = tar::Archive::new(decoder);
    archive.set_preserve_permissions(false);
    let mut bytes = 0;
    for entry in archive.entries().map_err(|e| format!("Cannot read the application archive: {e}"))? {
        let mut entry = entry.map_err(|e| format!("Invalid application archive entry: {e}"))?;
        let name = entry.path().map_err(|e| e.to_string())?.into_owned();
        if !(entry.header().entry_type().is_file() || entry.header().entry_type().is_dir()) {
            return Err(format!("Unsupported link or special file in application payload: {}", name.display()));
        }
        let size = entry.size();
        if !entry.unpack_in(destination).map_err(|e| format!("Cannot unpack {}: {e}", name.display()))? {
            return Err(format!("Payload path leaves the program folder: {}", name.display()));
        }
        bytes += size;
        completed(bytes);
    }
    // Reading through the decoder's end also observes a truncated HTTPS body or
    // zstd frame error after tar's end marker, before any old files are moved.
    std::io::copy(&mut archive.into_inner(), &mut std::io::sink()).map_err(|e| format!("The application download/archive did not finish: {e}"))?;
    if !destination.join("VRCForge.exe").is_file() {
        return Err("The application payload does not contain VRCForge.exe.".into());
    }
    let mut source = File::open(setup).map_err(|e| e.to_string())?.take(info.executable_bytes);
    let mut uninstaller = File::create(destination.join("Uninstall.exe")).map_err(|e| format!("Cannot create the new uninstaller: {e}"))?;
    std::io::copy(&mut source, &mut uninstaller).map_err(|e| format!("Cannot write the new uninstaller: {e}"))?;
    uninstaller.sync_all().map_err(|e| format!("Cannot finish writing the uninstaller: {e}"))?;
    Ok(())
}

pub fn install(app: &tauri::AppHandle, user: &platform::UserContext, request: &InstallRequest) -> Result<SetupResult, String> {
    install_inner(app, user, request).map_err(|error| {log(user, &format!("INSTALL ERROR: {error}")); error})
}

fn install_inner(app: &tauri::AppHandle, user: &platform::UserContext, request: &InstallRequest) -> Result<SetupResult, String> {
    let existing = platform::existing_install()?;
    // Upgrades keep the path recorded by the old installer. Changing a custom
    // input cannot silently strand an existing installation at another path.
    if let Some(old) = existing.as_ref() {
        if !same_path(Path::new(&request.install_dir), &old.install_dir) {
            return Err(format!("VRCForge is already installed at {}. Upgrade it at that location instead of creating a second installation.", old.install_dir.display()));
        }
    }
    let target = existing.as_ref().map(|old| old.install_dir.clone()).unwrap_or_else(|| PathBuf::from(&request.install_dir));
    validate_install_directory(user, &target, existing.as_ref())?;
    if !platform::running_processes(&target)?.is_empty() {
        return Err("VRCForge is running. Save your work, then choose Close and continue.".into());
    }
    let data = platform::data_dir(user)?;
    if request.fresh {require_scoped_directory(user, &data)?;}
    let info = inspect_payload()?;
    platform::check_writable(&target)?;
    let available = platform::disk_free(&target)?;
    if available < info.required_bytes {
        return Err(format!("Not enough free space: {} bytes required, {} available on the selected disk.", info.required_bytes, available));
    }
    fs::create_dir_all(target.parent().unwrap()).map_err(|e| format!("Cannot create the installation parent folder: {e}"))?;
    let stage = unique_sibling(&target, "stage")?;
    let backup = unique_sibling(&target, "previous")?;
    fs::create_dir(&stage).map_err(|e| format!("Cannot create staging folder {}: {e}", stage.display()))?;
    log(user, &format!("Install to {}; personal data {}; stage {}", target.display(), data.display(), stage.display()));
    progress(app, user, "extract", 5);
    let setup = std::env::current_exe().map_err(|e| e.to_string())?;
    let mut last_percent = 5;
    if let Err(error) = extract_payload(&setup, &info, &stage, |bytes| {
        let percent = (5 + bytes.saturating_mul(65) / info.app_bytes.max(1)).min(70) as u8;
        if percent > last_percent {last_percent = percent; progress(app, user, "extract", percent);}
    }) {
        return match remove_directory(&stage) {Ok(()) => Err(error), Err(cleanup) => Err(format!("{error} Staging cleanup also failed: {cleanup}"))};
    }
    let snapshot = match platform::snapshot_registration(user, &target) {
        Ok(snapshot) => snapshot,
        Err(error) => {
            let cleanup = remove_directory(&stage);
            return Err(format!("Cannot prepare existing registration for upgrade: {error}. {}", cleanup.err().unwrap_or_default()));
        }
    };
    progress(app, user, "activate", 75);
    let had_previous = activate_directory(&stage, &target, &backup)?;

    // A previous portable installation can keep data below its program folder.
    // Move it from backup to the identical absolute data path, never migrate it.
    let nested_relative = relative_under(&data, &target).map(Path::to_path_buf);
    let mut portable_data_moved = false;
    let mut cleared_backup: Option<PathBuf> = None;
    let finalized = (|| -> Result<(), String> {
        if let Some(relative) = nested_relative.as_ref() {
            let old_data = backup.join(relative);
            if had_previous && old_data.exists() && !request.fresh {
                if data.exists() { return Err(format!("The new payload unexpectedly contains the existing personal data path {}.", data.display())); }
                fs::create_dir_all(data.parent().unwrap()).map_err(|e| e.to_string())?;
                fs::rename(&old_data, &data).map_err(|e| format!("Cannot retain personal data at {}: {e}", data.display()))?;
                portable_data_moved = true;
            }
        } else if request.fresh && data.exists() {
            let retained_until_commit = unique_sibling(&data, "clearing")?;
            fs::rename(&data, &retained_until_commit).map_err(|e| format!("Cannot clear the requested personal data at {}: {e}", data.display()))?;
            cleared_backup = Some(retained_until_commit);
        }
        progress(app, user, "register", 85);
        platform::register_install(user, &target, env!("CARGO_PKG_VERSION"), info.required_bytes, request.desktop_shortcut, request.start_menu)?;
        Ok(())
    })();
    if let Err(error) = finalized {
        let mut restore_errors = Vec::new();
        if portable_data_moved {
            let old_data = backup.join(nested_relative.as_ref().unwrap());
            if let Err(e) = fs::rename(&data, &old_data) {restore_errors.push(format!("Cannot put personal data back into {}: {e}", old_data.display()));}
        }
        // Never delete the active new tree if it still holds the only copy of
        // retained portable data after a failed move back.
        if restore_errors.is_empty() {
            if let Err(e) = restore_program_directory(&target, &backup, had_previous) {restore_errors.push(e);}
        }
        if let Some(retained) = cleared_backup.as_ref() {
            // Registration can create an empty data directory; remove that only
            // if empty, then return the original data to its original path.
            if data.is_dir() {let _ = fs::remove_dir(&data);}
            if let Err(e) = fs::rename(retained, &data) {restore_errors.push(format!("Cannot restore personal data from {}: {e}", retained.display()));}
        }
        if let Err(e) = platform::restore_registration(user, &snapshot) {restore_errors.push(format!("Cannot restore installation registration/shortcuts: {e}"));}
        let restoration = if restore_errors.is_empty() {"Previous files, registration and personal data were restored.".to_owned()} else {format!("Recovery incomplete: {}. Previous program backup: {}", restore_errors.join("; "), backup.display())};
        return Err(format!("Setup could not finish: {error}. {restoration}"));
    }
    progress(app, user, "cleanup", 95);
    // Commit is complete. Cleanup failures must describe the actual new version
    // and the retained path, rather than claim the old version is untouched.
    if let Some(retained) = cleared_backup {
        remove_directory(&retained).map_err(|e| format!("VRCForge was installed, but the requested old personal data could not all be cleared: {e}"))?;
    }
    if had_previous {
        remove_directory(&backup).map_err(|e| format!("VRCForge was installed successfully, but the previous program backup remains: {e}"))?;
    }
    progress(app, user, "cleanup", 100);
    Ok(SetupResult {install_dir: target.to_string_lossy().into_owned(), data_dir: data.to_string_lossy().into_owned(), data_cleared: request.fresh, log_path: setup_log_path(user).to_string_lossy().into_owned()})
}

pub fn uninstall(app: &tauri::AppHandle, user: &platform::UserContext, request: &UninstallRequest) -> Result<SetupResult, String> {
    uninstall_inner(app, user, request).map_err(|error| {log(user, &format!("UNINSTALL ERROR: {error}")); error})
}

fn uninstall_inner(app: &tauri::AppHandle, user: &platform::UserContext, request: &UninstallRequest) -> Result<SetupResult, String> {
    let installed = platform::existing_install()?.ok_or("No VRCForge installation is registered. No files were removed.")?;
    let target = installed.install_dir;
    require_scoped_directory(user, &target)?;
    let data = platform::data_dir(user)?;
    if same_path(&data, &target) {return Err("The registered personal data path is the entire program folder. No files were removed.".into());}
    if !request.keep_data {require_scoped_directory(user, &data)?;}
    if !platform::running_processes(&target)?.is_empty() {
        return Err("VRCForge is running. Save your work, then choose Close and continue.".into());
    }
    let current = std::env::current_exe().map_err(|e| e.to_string())?;
    if relative_under(&current, &target).is_some() {return Err("The uninstaller is still running inside the program folder. Relaunch its temporary copy before removing files.".into());}
    log(user, &format!("Uninstall {}; keep personal data {}: {}", target.display(), data.display(), request.keep_data));
    progress(app, user, "uninstall", 10);
    if target.exists() {
        if request.keep_data && relative_under(&data, &target).is_some() {
            remove_program_files_preserving_data(&target, &data)?;
        } else {remove_directory(&target)?;}
    }
    progress(app, user, "uninstall", 70);
    if !request.keep_data {remove_directory(&data)?;}
    // Keep the uninstall entry available on any earlier file-removal failure.
    // A retry can finish the remaining files instead of stranding registration.
    platform::remove_registration(user, &target)?;
    progress(app, user, "uninstall", 100);
    Ok(SetupResult {install_dir:target.to_string_lossy().into_owned(), data_dir:data.to_string_lossy().into_owned(), data_cleared:!request.keep_data, log_path:setup_log_path(user).to_string_lossy().into_owned()})
}

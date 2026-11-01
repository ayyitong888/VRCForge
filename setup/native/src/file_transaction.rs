//! Same-volume file replacement and recovery. This module needs no Windows
//! administrator access, registry, UI, process or network capability.
use std::{fs, path::Path};

pub fn same_path(left: &Path, right: &Path) -> bool {
    // Windows paths are case-insensitive. This compares already absolute setup
    // locations, not a second identity or a file-content fingerprint.
    left.to_string_lossy().trim_end_matches(['\\', '/']).eq_ignore_ascii_case(right.to_string_lossy().trim_end_matches(['\\', '/']))
}

pub fn relative_under<'a>(child: &'a Path, parent: &Path) -> Option<&'a Path> {
    let mut rest = child.components();
    for component in parent.components() {
        let next = rest.next()?;
        if !next.as_os_str().to_string_lossy().eq_ignore_ascii_case(&component.as_os_str().to_string_lossy()) {return None;}
    }
    Some(rest.as_path())
}

pub fn remove_directory(path: &Path) -> Result<(), String> {
    if path.exists() {
        fs::remove_dir_all(path).map_err(|e| format!("Cannot remove {}: {e}", path.display()))?;
    }
    Ok(())
}

pub fn activate_directory(stage: &Path, target: &Path, backup: &Path) -> Result<bool, String> {
    let had_previous = target.exists();
    if had_previous {
        if let Err(error) = fs::rename(target, backup) {
            let cleanup = remove_directory(stage);
            return Err(format!("Cannot move the existing program folder; old files remain at {}: {error}. {}", target.display(), cleanup.err().unwrap_or_default()));
        }
    }
    if let Err(error) = fs::rename(stage, target) {
        let restored = if had_previous {fs::rename(backup, target).map_err(|e| format!("Could not restore old files from {}: {e}", backup.display()))} else {Ok(())};
        let cleanup = remove_directory(stage);
        return Err(format!("Cannot activate the new program files: {error}. {} {}", restored.err().unwrap_or_default(), cleanup.err().unwrap_or_default()));
    }
    Ok(had_previous)
}

pub fn restore_program_directory(target: &Path, backup: &Path, had_previous: bool) -> Result<(), String> {
    remove_directory(target)?;
    if had_previous {
        fs::rename(backup, target).map_err(|e| format!("Cannot restore prior program files from {}: {e}", backup.display()))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{path::PathBuf, time::{SystemTime, UNIX_EPOCH}};

    struct FilesystemCase(PathBuf);
    impl FilesystemCase {
        fn new(label: &str) -> Self {
            let root = std::env::temp_dir().join(format!("VRCForge-setup-replay-{label}-{}-{}", std::process::id(), SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos()));
            fs::create_dir(&root).unwrap();
            Self(root)
        }
        fn folder(&self, name: &str, marker: &str) -> PathBuf {
            let path = self.0.join(name);
            fs::create_dir(&path).unwrap();
            fs::write(path.join("VRCForge.exe"), marker).unwrap();
            path
        }
    }
    impl Drop for FilesystemCase {
        fn drop(&mut self) {let _ = fs::remove_dir_all(&self.0);}
    }

    #[test]
    fn failed_activation_restores_existing_files() {
        let case = FilesystemCase::new("missing-stage");
        let target = case.folder("app", "old");
        let backup = case.0.join("previous");
        let error = activate_directory(&case.0.join("missing-stage"), &target, &backup).unwrap_err();
        assert!(error.contains("Cannot activate"));
        assert_eq!(fs::read_to_string(target.join("VRCForge.exe")).unwrap(), "old");
        assert!(!backup.exists());
    }

    #[test]
    fn failed_finalization_can_restore_old_files_before_cleanup() {
        let case = FilesystemCase::new("finalization");
        let target = case.folder("app", "old");
        let stage = case.folder("stage", "new");
        let backup = case.0.join("previous");
        let had_previous = activate_directory(&stage, &target, &backup).unwrap();
        assert_eq!(fs::read_to_string(backup.join("VRCForge.exe")).unwrap(), "old");
        // Production takes this path when shortcut/registry finalization fails.
        restore_program_directory(&target, &backup, had_previous).unwrap();
        assert_eq!(fs::read_to_string(target.join("VRCForge.exe")).unwrap(), "old");
        assert!(!backup.exists());
    }

    #[test]
    fn portable_uninstall_keeps_data_at_the_same_path() {
        let case = FilesystemCase::new("portable-data");
        let target = case.folder("app", "program");
        let data = target.join("Data");
        fs::create_dir(&data).unwrap();
        fs::write(data.join("chat.json"), "keep").unwrap();
        remove_program_files_preserving_data(&target, &target.join("data")).unwrap();
        assert!(!target.join("VRCForge.exe").exists());
        assert_eq!(fs::read_to_string(data.join("chat.json")).unwrap(), "keep");
    }

    #[test]
    fn portable_data_prefix_uses_windows_case_rules() {
        assert_eq!(relative_under(Path::new(r"c:\program files\VRCForge\Data"), Path::new(r"C:\Program Files\vrcforge")), Some(Path::new("Data")));
        assert!(relative_under(Path::new(r"C:\VRCForge-other\data"), Path::new(r"C:\VRCForge")).is_none());
    }
}

pub fn remove_program_files_preserving_data(directory: &Path, data: &Path) -> Result<(), String> {
    for entry in fs::read_dir(directory).map_err(|e| format!("Cannot list {}: {e}", directory.display()))? {
        let entry = entry.map_err(|e| e.to_string())?;
        let path = entry.path();
        if same_path(&path, data) {continue;}
        if entry.file_type().map_err(|e| e.to_string())?.is_dir() {
            if relative_under(data, &path).is_some() {
                remove_program_files_preserving_data(&path, data)?;
                // Parent directories containing the kept data remain in place.
            } else {remove_directory(&path)?;}
        } else {fs::remove_file(&path).map_err(|e| format!("Cannot remove {}: {e}", path.display()))?;}
    }
    Ok(())
}

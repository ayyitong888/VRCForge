//! Windows integration for the single-window setup program.
//!
//! The installer's elevated identity owns machine changes. Explorer's existing
//! token selects the interactive user's profile and launches the App unelevated.
//! Handles, COM apartments and environment blocks are scoped to
//! their operation; this module creates no service, pipe or authentication layer.

use std::{
    fs,
    io::{ErrorKind, Write},
    mem::{size_of, zeroed},
    os::windows::ffi::OsStrExt,
    path::{Path, PathBuf},
    ptr,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use windows::{
    core::{Interface, GUID, PCWSTR, PWSTR},
    Win32::{
        Foundation::{CloseHandle, GetLastError, HANDLE, HLOCAL, HWND, LPARAM, LocalFree,
            SetLastError, WPARAM, ERROR_ACCESS_DENIED, ERROR_NOT_ALL_ASSIGNED, ERROR_SUCCESS},
        Globalization::GetUserDefaultUILanguage,
        Security::{
            Authorization::{ConvertSidToStringSidW, ConvertStringSecurityDescriptorToSecurityDescriptorW,
                GetNamedSecurityInfoW, SetNamedSecurityInfoW, SDDL_REVISION_1, SE_FILE_OBJECT},
            AdjustTokenPrivileges, DuplicateTokenEx, GetSecurityDescriptorControl,
            GetSecurityDescriptorDacl, GetTokenInformation, LookupPrivilegeValueW, LUID_AND_ATTRIBUTES,
            SecurityImpersonation, TokenPrimary, TokenUser, TOKEN_ASSIGN_PRIMARY,
            TOKEN_DUPLICATE, TOKEN_IMPERSONATE, TOKEN_QUERY, TOKEN_USER,
            TOKEN_ADJUST_PRIVILEGES, TOKEN_PRIVILEGES, SE_DEBUG_NAME, SE_PRIVILEGE_ENABLED,
            DACL_SECURITY_INFORMATION, PROTECTED_DACL_SECURITY_INFORMATION,
            UNPROTECTED_DACL_SECURITY_INFORMATION, SE_DACL_PROTECTED, PSECURITY_DESCRIPTOR,
        },
        Storage::FileSystem::GetDiskFreeSpaceExW,
        System::{
            Com::{CoCreateInstance, CoInitializeEx, CoTaskMemFree, CoUninitialize,
                IPersistFile, CLSCTX_INPROC_SERVER, COINIT_APARTMENTTHREADED, STGM_READ},
            Diagnostics::ToolHelp::{CreateToolhelp32Snapshot, Process32FirstW, Process32NextW,
                PROCESSENTRY32W, TH32CS_SNAPPROCESS},
            Environment::{CreateEnvironmentBlock, DestroyEnvironmentBlock},
            Threading::{CreateProcessWithTokenW, GetCurrentProcess, GetCurrentProcessId, OpenProcess,
                OpenProcessToken, QueryFullProcessImageNameW, TerminateProcess,
                WaitForSingleObject, CREATE_UNICODE_ENVIRONMENT, LOGON_WITH_PROFILE,
                PROCESS_INFORMATION, PROCESS_NAME_WIN32, PROCESS_QUERY_LIMITED_INFORMATION,
                PROCESS_TERMINATE, STARTUPINFOW, PROCESS_SYNCHRONIZE},
        },
        UI::{
            Shell::{FileOpenDialog, IFileOpenDialog, IShellLinkW, ShellLink,
                SHChangeNotify, SHGetKnownFolderPath, FOLDERID_CommonPrograms,
                FOLDERID_Desktop, FOLDERID_LocalAppData, FOLDERID_Profile,
                FOLDERID_ProgramFilesX64, FOLDERID_Programs, FOLDERID_PublicDesktop,
                FOS_FORCEFILESYSTEM, FOS_PICKFOLDERS, KF_FLAG_DEFAULT,
                SHCNE_ASSOCCHANGED, SHCNF_IDLIST, SIGDN_FILESYSPATH, SLGP_RAWPATH},
            WindowsAndMessaging::{EnumWindows, GetShellWindow, GetWindowThreadProcessId,
                PostMessageW, MessageBoxW, IDOK, MB_ICONINFORMATION, MB_OKCANCEL, MB_SETFOREGROUND, WM_CLOSE},
        },
    },
};
use winreg::{enums::*, RegKey, RegValue};

const UNINSTALL_KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\VRCForge";
const USER_KEY: &str = r"Software\VRCForge";
const VSK_PROGID: &str = "VRCForge.SkillPackage";
const EDGE_CLIENT: &str = r"Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}";

struct OwnedHandle(usize);
impl OwnedHandle {
    fn new(handle: HANDLE) -> Self { Self(handle.0 as usize) }
    fn get(&self) -> HANDLE { HANDLE(self.0 as _) }
}
impl Drop for OwnedHandle {
    fn drop(&mut self) { unsafe { let _ = CloseHandle(self.get()); } }
}

struct DebugPrivilege {
    token: OwnedHandle,
    previous: TOKEN_PRIVILEGES,
    active: bool,
}
impl DebugPrivilege {
    fn enable() -> Result<Self, String> {
        unsafe {
            let mut token = HANDLE::default();
            OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY | TOKEN_ADJUST_PRIVILEGES, &mut token)
                .map_err(|error| win_error("Could not use the installer's Windows privileges", error))?;
            let token = OwnedHandle::new(token);
            let mut luid = zeroed();
            LookupPrivilegeValueW(None, SE_DEBUG_NAME, &mut luid)
                .map_err(|error| win_error("Could not resolve the Windows cross-account privilege", error))?;
            let requested = TOKEN_PRIVILEGES { PrivilegeCount: 1,
                Privileges: [LUID_AND_ATTRIBUTES { Luid: luid, Attributes: SE_PRIVILEGE_ENABLED }] };
            let mut previous: TOKEN_PRIVILEGES = zeroed();
            let mut returned = 0;
            SetLastError(ERROR_SUCCESS);
            AdjustTokenPrivileges(token.get(), false, Some(&requested), size_of::<TOKEN_PRIVILEGES>() as u32,
                Some(&mut previous), Some(&mut returned))
                .map_err(|error| win_error("Could not obtain the logged-in user's Explorer token", error))?;
            if GetLastError() == ERROR_NOT_ALL_ASSIGNED {
                return Err("Windows denied access to the logged-in user's Explorer token. The administrator account does not have the required SeDebugPrivilege; use an administrator allowed to access this desktop session.".into());
            }
            Ok(Self { token, previous, active: true })
        }
    }
    fn restore(&mut self) -> Result<(), String> {
        if self.active && self.previous.PrivilegeCount != 0 {
            unsafe { AdjustTokenPrivileges(self.token.get(), false, Some(&self.previous), 0, None, None) }
                .map_err(|error| win_error("Could not restore the installer's temporary Windows privilege", error))?;
        }
        self.active = false;
        Ok(())
    }
}
impl Drop for DebugPrivilege {
    fn drop(&mut self) { let _ = self.restore(); }
}

fn explorer_token(pid: u32) -> windows::core::Result<OwnedHandle> {
    unsafe {
        let process = OwnedHandle::new(OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid)?);
        let mut token = HANDLE::default();
        OpenProcessToken(process.get(), TOKEN_QUERY | TOKEN_DUPLICATE | TOKEN_IMPERSONATE | TOKEN_ASSIGN_PRIMARY, &mut token)?;
        Ok(OwnedHandle::new(token))
    }
}

pub struct UserContext {
    pub sid: String,
    pub local_app_data: PathBuf,
    pub desktop: PathBuf,
    pub start_menu: PathBuf,
    pub profile: PathBuf,
    token: OwnedHandle,
}

pub struct InstalledApp {
    pub install_dir: PathBuf,
    pub version: String,
}

fn wide(value: impl AsRef<std::ffi::OsStr>) -> Vec<u16> {
    value.as_ref().encode_wide().chain(Some(0)).collect()
}
fn win_error(context: &str, error: impl std::fmt::Display) -> String {
    format!("{context}: {error}")
}
fn io_error(context: &str, path: &Path, error: std::io::Error) -> String {
    format!("{context} '{}': {error}", path.display())
}

fn known_folder(id: &GUID, token: Option<HANDLE>) -> Result<PathBuf, String> {
    unsafe {
        let text = SHGetKnownFolderPath(id, KF_FLAG_DEFAULT, token)
            .map_err(|error| win_error("Could not resolve the Windows user folder", error))?;
        let result = text.to_string().map(PathBuf::from)
            .map_err(|error| win_error("The Windows folder path is invalid", error));
        CoTaskMemFree(Some(text.0 as _));
        result
    }
}

pub fn resolve_user() -> Result<UserContext, String> {
    unsafe {
        let shell = GetShellWindow();
        if shell.0.is_null() {
            return Err("Windows Explorer is unavailable. Run setup from the logged-in Windows desktop.".into());
        }
        let mut pid = 0;
        GetWindowThreadProcessId(shell, Some(&mut pid));
        let source = match explorer_token(pid) {
            Ok(token) => token,
            Err(error) if error.code() == windows::core::HRESULT::from_win32(ERROR_ACCESS_DENIED.0) => {
                // OpenProcessToken documents SeDebugPrivilege for another account.
                // Enable it only after actual access denial, acquire this session's
                // Explorer token, then immediately restore the original privilege.
                let mut privilege = DebugPrivilege::enable()?;
                let token = explorer_token(pid);
                privilege.restore()?;
                token.map_err(|error| win_error("Could not obtain the logged-in user's Explorer token", error))?
            },
            Err(error) => return Err(win_error("Could not obtain the logged-in user's Explorer token", error)),
        };
        let mut primary = HANDLE::default();
        DuplicateTokenEx(source.get(), TOKEN_QUERY | TOKEN_DUPLICATE | TOKEN_IMPERSONATE | TOKEN_ASSIGN_PRIMARY,
            None, SecurityImpersonation, TokenPrimary, &mut primary)
            .map_err(|error| win_error("Could not duplicate the Explorer token for App launch", error))?;
        let token = OwnedHandle::new(primary);
        let mut bytes = 0;
        let _ = GetTokenInformation(token.get(), TokenUser, None, 0, &mut bytes);
        let mut info = vec![0usize; (bytes as usize).div_ceil(size_of::<usize>())];
        GetTokenInformation(token.get(), TokenUser, Some(info.as_mut_ptr() as _), bytes, &mut bytes)
            .map_err(|error| win_error("Could not read the logged-in user", error))?;
        let user = &*(info.as_ptr() as *const TOKEN_USER);
        let mut sid_text = PWSTR::null();
        ConvertSidToStringSidW(user.User.Sid, &mut sid_text)
            .map_err(|error| win_error("Could not read the logged-in user's registry identity", error))?;
        let sid_result = sid_text.to_string().map_err(|error| win_error("Invalid user registry identity", error));
        LocalFree(Some(HLOCAL(sid_text.0 as _)));
        let sid = sid_result?;
        let local_app_data = known_folder(&FOLDERID_LocalAppData, Some(token.get()))?;
        let desktop = known_folder(&FOLDERID_Desktop, Some(token.get()))?;
        let start_menu = known_folder(&FOLDERID_Programs, Some(token.get()))?;
        let profile = known_folder(&FOLDERID_Profile, Some(token.get()))?;
        Ok(UserContext { sid, local_app_data, desktop, start_menu, profile, token })
    }
}

pub fn existing_install() -> Result<Option<InstalledApp>, String> {
    let key = match RegKey::predef(HKEY_LOCAL_MACHINE).open_subkey_with_flags(UNINSTALL_KEY, KEY_READ | KEY_WOW64_64KEY) {
        Ok(key) => key,
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(format!("Could not read the existing VRCForge installation: {error}")),
    };
    let directory: String = key.get_value("InstallLocation")
        .map_err(|error| format!("The existing VRCForge registration has no readable InstallLocation: {error}"))?;
    let version = key.get_value("DisplayVersion").unwrap_or_else(|_| "Unknown".to_owned());
    Ok(Some(InstalledApp { install_dir: PathBuf::from(directory), version }))
}

pub fn default_install_dir() -> PathBuf {
    known_folder(&FOLDERID_ProgramFilesX64, None)
        .unwrap_or_else(|_| PathBuf::from(r"C:\Program Files")).join("VRCForge")
}

pub fn data_dir(user: &UserContext) -> Result<PathBuf, String> {
    match RegKey::predef(HKEY_USERS).open_subkey_with_flags(format!(r"{}\{USER_KEY}", user.sid), KEY_READ | KEY_WOW64_64KEY) {
        Ok(key) => match key.get_value::<String, _>("DataDir") {
            Ok(path) if !path.trim().is_empty() => {
                let path = path.replace("%LOCALAPPDATA%", &user.local_app_data.to_string_lossy())
                    .replace("%USERPROFILE%", &user.profile.to_string_lossy());
                Ok(PathBuf::from(path))
            },
            Ok(_) => Ok(user.local_app_data.join("VRCForge").join("agentic-app")),
            Err(error) if error.kind() == ErrorKind::NotFound => Ok(user.local_app_data.join("VRCForge").join("agentic-app")),
            Err(error) => Err(format!("Could not read the saved VRCForge data location: {error}")),
        },
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(user.local_app_data.join("VRCForge").join("agentic-app")),
        Err(error) => Err(format!("Could not read the logged-in user's VRCForge settings: {error}")),
    }
}

fn existing_parent(path: &Path) -> Result<&Path, String> {
    let mut parent = path;
    while !parent.exists() {
        parent = parent.parent().ok_or_else(|| format!("No existing drive or parent folder for '{}'.", path.display()))?;
    }
    if !parent.is_dir() { return Err(format!("'{}' is a file, not an installation folder.", parent.display())); }
    Ok(parent)
}

pub fn disk_free(path: &Path) -> Result<u64, String> {
    let parent = existing_parent(path)?;
    let text = wide(parent);
    let mut available = 0;
    unsafe { GetDiskFreeSpaceExW(PCWSTR(text.as_ptr()), Some(&mut available), None, None) }
        .map_err(|error| win_error("Could not read available disk space", error))?;
    Ok(available)
}

pub fn check_writable(path: &Path) -> Result<(), String> {
    let parent = existing_parent(path)?;
    let probe = parent.join(format!(".vrcforge-write-check-{}-{}", std::process::id(),
        SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_nanos()));
    // The only probe belongs to this call and is removed before returning.
    let mut file = fs::OpenOptions::new().write(true).create_new(true).open(&probe)
        .map_err(|error| io_error("This folder cannot be written to", parent, error))?;
    let written = file.write_all(b"VRCForge setup write check");
    drop(file);
    fs::remove_file(&probe).map_err(|error| io_error("Could not remove the temporary write check", &probe, error))?;
    written.map_err(|error| io_error("This folder cannot be written to", parent, error))
}

pub fn running_processes(install: &Path) -> Result<Vec<u32>, String> {
    unsafe {
        let snapshot = OwnedHandle::new(CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
            .map_err(|error| win_error("Could not inspect running applications", error))?);
        let mut entry: PROCESSENTRY32W = zeroed();
        entry.dwSize = size_of::<PROCESSENTRY32W>() as u32;
        let mut found = Vec::new();
        if Process32FirstW(snapshot.get(), &mut entry).is_err() { return Ok(found); }
        loop {
            let name = String::from_utf16_lossy(&entry.szExeFile[..entry.szExeFile.iter().position(|value| *value == 0).unwrap_or(entry.szExeFile.len())]);
            if entry.th32ProcessID != GetCurrentProcessId()
                && (name.eq_ignore_ascii_case("VRCForge.exe") || name.eq_ignore_ascii_case("vrcforge_backend.exe"))
            {
                if let Ok(process) = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, entry.th32ProcessID) {
                    let process = OwnedHandle::new(process);
                    let mut buffer = vec![0u16; 32768];
                    let mut length = buffer.len() as u32;
                    if QueryFullProcessImageNameW(process.get(), PROCESS_NAME_WIN32, PWSTR(buffer.as_mut_ptr()), &mut length).is_ok() {
                        let image = PathBuf::from(String::from_utf16_lossy(&buffer[..length as usize]));
                        if image.parent().is_some_and(|parent| parent.eq_ignore_ascii_case(install))
                            || image.parent().and_then(Path::parent).is_some_and(|parent| parent.eq_ignore_ascii_case(install))
                        { found.push(entry.th32ProcessID); }
                    }
                }
            }
            if Process32NextW(snapshot.get(), &mut entry).is_err() { break; }
        }
        Ok(found)
    }
}

// Case-insensitive Windows path comparison without hashing or reopening files.
trait WindowsPathEq { fn eq_ignore_ascii_case(&self, other: &Path) -> bool; }
impl WindowsPathEq for Path {
    fn eq_ignore_ascii_case(&self, other: &Path) -> bool {
        self.as_os_str().to_string_lossy().trim_end_matches(['\\', '/'])
            .eq_ignore_ascii_case(other.as_os_str().to_string_lossy().trim_end_matches(['\\', '/']))
    }
}

unsafe extern "system" fn close_app_window(window: HWND, context: LPARAM) -> windows::core::BOOL {
    let targets = unsafe { &*(context.0 as *const Vec<u32>) };
    let mut pid = 0;
    unsafe { GetWindowThreadProcessId(window, Some(&mut pid)); }
    if targets.contains(&pid) { unsafe { let _ = PostMessageW(Some(window), WM_CLOSE, WPARAM(0), LPARAM(0)); } }
    true.into()
}

pub fn close_running(install: &Path) -> Result<(), String> {
    let targets = running_processes(install)?;
    if targets.is_empty() { return Ok(()); }
    unsafe { EnumWindows(Some(close_app_window), LPARAM(&targets as *const Vec<u32> as isize)) }
        .map_err(|error| win_error("Could not ask VRCForge to close", error))?;
    let deadline = Instant::now() + Duration::from_secs(4);
    while Instant::now() < deadline {
        if running_processes(install)?.is_empty() { return Ok(()); }
        std::thread::sleep(Duration::from_millis(100));
    }
    // Re-enumerate the selected installation, never kill a different App/profile.
    for pid in running_processes(install)? {
        unsafe {
            let process = OwnedHandle::new(OpenProcess(PROCESS_TERMINATE | PROCESS_SYNCHRONIZE, false, pid)
                .map_err(|error| win_error("VRCForge could not be closed", error))?);
            TerminateProcess(process.get(), 0).map_err(|error| win_error("VRCForge could not be closed", error))?;
            if WaitForSingleObject(process.get(), 5000).0 != 0 {
                return Err(format!("VRCForge process {pid} has not exited. Close it and retry."));
            }
        }
    }
    Ok(())
}

struct ComScope(bool);
impl ComScope {
    fn new() -> Result<Self, String> {
        let result = unsafe { CoInitializeEx(None, COINIT_APARTMENTTHREADED) };
        // An already initialized MTA can create the free-threaded shell links.
        if result.0 == 0x80010106u32 as i32 { return Ok(Self(false)); }
        result.ok().map_err(|error| win_error("Could not initialize Windows shell integration", error))?;
        Ok(Self(true))
    }
}
impl Drop for ComScope { fn drop(&mut self) { if self.0 { unsafe { CoUninitialize(); } } } }

fn create_shortcut(path: &Path, executable: &Path, arguments: &str, directory: &Path) -> Result<(), String> {
    let _com = ComScope::new()?;
    if path.exists() && !shortcut_owned_by(path, directory)? {
        return Err(format!("The shortcut '{}' points to another location and was not overwritten. Rename it or disable this shortcut option and retry.", path.display()));
    }
    fs::create_dir_all(path.parent().ok_or("The shortcut folder is missing.")?)
        .map_err(|error| io_error("Could not create the shortcut folder", path, error))?;
    let file = wide(path); let target = wide(executable); let args = wide(arguments); let work = wide(directory);
    unsafe {
        let link: IShellLinkW = CoCreateInstance(&ShellLink, None, CLSCTX_INPROC_SERVER)
            .map_err(|error| win_error("Could not create a Windows shortcut", error))?;
        link.SetPath(PCWSTR(target.as_ptr())).map_err(|error| win_error("Could not set the shortcut target", error))?;
        link.SetWorkingDirectory(PCWSTR(work.as_ptr())).map_err(|error| win_error("Could not set the shortcut folder", error))?;
        link.SetArguments(PCWSTR(args.as_ptr())).map_err(|error| win_error("Could not set shortcut arguments", error))?;
        link.SetIconLocation(PCWSTR(target.as_ptr()), 0).map_err(|error| win_error("Could not set the shortcut icon", error))?;
        let persist: IPersistFile = link.cast().map_err(|error| win_error("Could not save the Windows shortcut", error))?;
        persist.Save(PCWSTR(file.as_ptr()), true).map_err(|error| win_error("Could not save the Windows shortcut", error))?;
    }
    Ok(())
}

fn shortcut_owned_by(path: &Path, directory: &Path) -> Result<bool, String> {
    let _com = ComScope::new()?;
    let file = wide(path);
    unsafe {
        let link: IShellLinkW = CoCreateInstance(&ShellLink, None, CLSCTX_INPROC_SERVER)
            .map_err(|error| win_error("Could not inspect the existing shortcut", error))?;
        let persist: IPersistFile = link.cast().map_err(|error| win_error("Could not inspect the existing shortcut", error))?;
        // A non-link or an unreadable replacement is not a setup-owned shortcut.
        if persist.Load(PCWSTR(file.as_ptr()), STGM_READ).is_err() { return Ok(false); }
        let mut buffer = vec![0u16; 32768];
        if link.GetPath(&mut buffer, ptr::null_mut(), SLGP_RAWPATH.0 as u32).is_err() { return Ok(false); }
        let length = buffer.iter().position(|value| *value == 0).unwrap_or(buffer.len());
        let target = PathBuf::from(String::from_utf16_lossy(&buffer[..length]));
        Ok(target.parent().is_some_and(|parent| parent.eq_ignore_ascii_case(directory))
            && target.file_name().is_some_and(|name| {
                name.eq_ignore_ascii_case("VRCForge.exe") || name.eq_ignore_ascii_case("Uninstall.exe")
            }))
    }
}

fn shortcut_paths(user: &UserContext) -> Result<Vec<PathBuf>, String> {
    let mut paths = Vec::new();
    for desktop in [&user.desktop, &known_folder(&FOLDERID_PublicDesktop, None)?] {
        paths.push(desktop.join("VRCForge.lnk"));
    }
    for programs in [&user.start_menu, &known_folder(&FOLDERID_CommonPrograms, None)?] {
        for name in ["VRCForge.lnk", "Uninstall VRCForge.lnk", "卸载 VRCForge.lnk", "解除安裝 VRCForge.lnk", "VRCForge をアンインストール.lnk"] {
            paths.push(programs.join("VRCForge").join(name));
        }
    }
    paths.sort(); paths.dedup();
    Ok(paths)
}

fn remove_file_if_present(path: &Path) -> Result<(), String> {
    match fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(()),
        Err(error) => Err(io_error("Could not remove the VRCForge shortcut", path, error)),
    }
}

fn remove_product_shortcuts(user: &UserContext, directory: &Path) -> Result<(), String> {
    for path in shortcut_paths(user)? {
        if path.exists() && shortcut_owned_by(&path, directory)? { remove_file_if_present(&path)?; }
    }
    for programs in [&user.start_menu, &known_folder(&FOLDERID_CommonPrograms, None)?] {
        let group = programs.join("VRCForge");
        if group.is_dir() && fs::read_dir(&group).map_err(|error| io_error("Could not read the shortcut group", &group, error))?.next().is_none() {
            fs::remove_dir(&group).map_err(|error| io_error("Could not remove the empty shortcut group", &group, error))?;
        }
    }
    Ok(())
}

struct SavedDacl {
    descriptor: usize,
    dacl: usize,
    protected: bool,
}
impl SavedDacl {
    fn from_raw(descriptor: PSECURITY_DESCRIPTOR) -> Result<Self, String> {
        let mut saved = Self { descriptor: descriptor.0 as usize, dacl: 0, protected: false };
        unsafe {
            let mut present = windows::core::BOOL(0);
            let mut defaulted = windows::core::BOOL(0);
            let mut dacl = ptr::null_mut();
            GetSecurityDescriptorDacl(descriptor, &mut present, &mut dacl, &mut defaulted)
                .map_err(|error| win_error("Could not read the folder permissions", error))?;
            let mut control = 0;
            let mut revision = 0;
            GetSecurityDescriptorControl(descriptor, &mut control, &mut revision)
                .map_err(|error| win_error("Could not read folder inheritance", error))?;
            saved.dacl = dacl as usize;
            saved.protected = control & SE_DACL_PROTECTED.0 != 0;
        }
        Ok(saved)
    }
    fn read(path: &Path) -> Result<Self, String> {
        let name = wide(path);
        let mut descriptor = PSECURITY_DESCRIPTOR::default();
        let result = unsafe { GetNamedSecurityInfoW(PCWSTR(name.as_ptr()), SE_FILE_OBJECT,
            DACL_SECURITY_INFORMATION, None, None, None, None, &mut descriptor) };
        if result != ERROR_SUCCESS {
            return Err(format!("Could not preserve permissions on '{}': {}", path.display(),
                std::io::Error::from_raw_os_error(result.0 as i32)));
        }
        Self::from_raw(descriptor)
    }
    fn from_sddl(text: &str) -> Result<Self, String> {
        let text = wide(text);
        let mut descriptor = PSECURITY_DESCRIPTOR::default();
        unsafe { ConvertStringSecurityDescriptorToSecurityDescriptorW(PCWSTR(text.as_ptr()),
            SDDL_REVISION_1, &mut descriptor, None) }
            .map_err(|error| win_error("Could not prepare VRCForge folder permissions", error))?;
        Self::from_raw(descriptor)
    }
    fn apply(&self, path: &Path, protected: bool) -> Result<(), String> {
        let name = wide(path);
        let inheritance = if protected { PROTECTED_DACL_SECURITY_INFORMATION }
            else { UNPROTECTED_DACL_SECURITY_INFORMATION };
        let result = unsafe { SetNamedSecurityInfoW(PCWSTR(name.as_ptr()), SE_FILE_OBJECT,
            DACL_SECURITY_INFORMATION | inheritance, None, None, Some(self.dacl as _), None) };
        if result != ERROR_SUCCESS {
            return Err(format!("Could not set permissions on '{}': {}", path.display(),
                std::io::Error::from_raw_os_error(result.0 as i32)));
        }
        Ok(())
    }
}
impl Drop for SavedDacl {
    fn drop(&mut self) { unsafe { LocalFree(Some(HLOCAL(self.descriptor as _))); } }
}

fn below_directory(path: &Path, directory: &Path) -> bool {
    let normalized = |value: &Path| value.to_string_lossy().replace('/', "\\").trim_end_matches('\\').to_lowercase();
    normalized(path).starts_with(&(normalized(directory) + "\\"))
}

fn set_program_permissions(user: &UserContext, directory: &Path) -> Result<(), String> {
    let data = data_dir(user)?;
    if below_directory(&data, directory) {
        if data.exists() {
            // A retained portable data tree must not inherit the program's RX
            // permissions. Preserve its ACEs exactly and protect only its root.
            let previous = SavedDacl::read(&data)?;
            previous.apply(&data, true)?;
        } else {
            fs::create_dir_all(&data).map_err(|error| io_error("Could not create personal data", &data, error))?;
            SavedDacl::from_sddl(&format!("D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;{})", user.sid))?
                .apply(&data, true)?;
        }
    }
    // The engine has just extracted a new program tree; tar does not import
    // Windows ACLs. Standard inheritance applies this DACL to its new files,
    // while the protected retained-data subtree above keeps its prior access.
    SavedDacl::from_sddl("D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FRFX;;;BU)")?
        .apply(directory, true)
}

pub fn register_install(user: &UserContext, directory: &Path, version: &str, size_bytes: u64, desktop: bool, start_menu: bool) -> Result<(), String> {
    set_program_permissions(user, directory)?;
    let machine = RegKey::predef(HKEY_LOCAL_MACHINE);
    let (key, _) = machine.create_subkey_with_flags(UNINSTALL_KEY, KEY_WRITE | KEY_WOW64_64KEY)
        .map_err(|error| format!("Could not register VRCForge in Apps & features: {error}"))?;
    for (name, value) in [
        ("DisplayName", "VRCForge".to_owned()), ("DisplayVersion", version.to_owned()),
        ("Publisher", "VRCForge".to_owned()), ("InstallLocation", directory.to_string_lossy().into_owned()),
        ("DisplayIcon", directory.join("VRCForge.exe").to_string_lossy().into_owned()),
        ("UninstallString", format!("\"{}\" --uninstall", directory.join("Uninstall.exe").display())),
    ] { key.set_value(name, &value).map_err(|error| format!("Could not write install registration '{name}': {error}"))?; }
    for (name, value) in [("EstimatedSize", (size_bytes / 1024).min(u32::MAX as u64) as u32), ("NoModify", 1), ("NoRepair", 1)] {
        key.set_value(name, &value).map_err(|error| format!("Could not write install registration '{name}': {error}"))?;
    }
    let users = RegKey::predef(HKEY_USERS);
    let (settings, _) = users.create_subkey_with_flags(format!(r"{}\{USER_KEY}", user.sid), KEY_WRITE | KEY_WOW64_64KEY)
        .map_err(|error| format!("Could not save the logged-in user's data location: {error}"))?;
    settings.set_value("DataDir", &data_dir(user)?.to_string_lossy().into_owned())
        .map_err(|error| format!("Could not save the logged-in user's data location: {error}"))?;
    let (app_id, _) = users.create_subkey_with_flags(
        format!(r"{}\Software\Classes\AppUserModelId\app.vrcforge.agentic", user.sid),
        KEY_WRITE | KEY_WOW64_64KEY,
    ).map_err(|error| format!("Could not register VRCForge's taskbar identity: {error}"))?;
    for (name, value) in [("DisplayName", "VRCForge".to_owned()),
        ("IconBackgroundColor", "0".to_owned()),
        ("IconUri", directory.join("VRCForge.png").to_string_lossy().into_owned())] {
        app_id.set_value(name, &value).map_err(|error| format!("Could not register VRCForge taskbar field '{name}': {error}"))?;
    }
    remove_product_shortcuts(user, directory)?;
    if desktop { create_shortcut(&user.desktop.join("VRCForge.lnk"), &directory.join("VRCForge.exe"), "", directory)?; }
    if start_menu {
        let group = user.start_menu.join("VRCForge");
        create_shortcut(&group.join("VRCForge.lnk"), &directory.join("VRCForge.exe"), "", directory)?;
        create_shortcut(&group.join("Uninstall VRCForge.lnk"), &directory.join("Uninstall.exe"), "--uninstall", directory)?;
    }
    register_vsk(directory)?;
    Ok(())
}

fn register_vsk(directory: &Path) -> Result<(), String> {
    let classes = RegKey::predef(HKEY_LOCAL_MACHINE).open_subkey_with_flags(r"Software\Classes", KEY_READ | KEY_WRITE | KEY_WOW64_64KEY)
        .map_err(|error| format!("Could not open Windows file associations: {error}"))?;
    let command = format!("\"{}\" \"%1\"", directory.join("VRCForge.exe").display());
    let command_path = format!(r"{VSK_PROGID}\shell\open\command");
    let (key, _) = classes.create_subkey(&command_path).map_err(|error| format!("Could not register .vsk files: {error}"))?;
    key.set_value("", &command).map_err(|error| format!("Could not register .vsk opening: {error}"))?;
    let (key, _) = classes.create_subkey(VSK_PROGID).map_err(|error| format!("Could not register .vsk files: {error}"))?;
    key.set_value("", &"VRCForge Skill Package").map_err(|error| format!("Could not name the .vsk file association: {error}"))?;
    let (key, _) = classes.create_subkey(format!(r"{VSK_PROGID}\DefaultIcon")).map_err(|error| format!("Could not register the .vsk icon: {error}"))?;
    key.set_value("", &format!("\"{}\",0", directory.join("VRCForge.exe").display())).map_err(|error| format!("Could not register the .vsk icon: {error}"))?;
    let (extension, _) = classes.create_subkey(".vsk").map_err(|error| format!("Could not register .vsk files: {error}"))?;
    let selected = extension.get_value::<String, _>("").unwrap_or_default();
    if selected.is_empty() || selected == VSK_PROGID { extension.set_value("", &VSK_PROGID).map_err(|error| format!("Could not select the .vsk file association: {error}"))?; }
    let (open_with, _) = extension.create_subkey("OpenWithProgids").map_err(|error| format!("Could not register .vsk Open With: {error}"))?;
    open_with.set_value(VSK_PROGID, &"").map_err(|error| format!("Could not register .vsk Open With: {error}"))?;
    unsafe { SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None); }
    Ok(())
}

fn delete_key_if_present(root: &RegKey, path: &str) -> Result<(), String> {
    match root.delete_subkey_all(path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(()),
        Err(error) => Err(format!("Could not remove VRCForge registry key '{path}': {error}")),
    }
}

pub fn remove_registration(user: &UserContext, directory: &Path) -> Result<(), String> {
    remove_product_shortcuts(user, directory)?;
    let machine = RegKey::predef(HKEY_LOCAL_MACHINE).open_subkey_with_flags("Software", KEY_READ | KEY_WRITE | KEY_WOW64_64KEY)
        .map_err(|error| format!("Could not open the machine installation registration: {error}"))?;
    let classes = machine.open_subkey_with_flags("Classes", KEY_READ | KEY_WRITE)
        .map_err(|error| format!("Could not open file associations for removal: {error}"))?;
    let command_path = format!(r"{VSK_PROGID}\shell\open\command");
    let command = classes.open_subkey(&command_path).and_then(|key| key.get_value::<String, _>("")).unwrap_or_default();
    let expected = format!("\"{}\" \"%1\"", directory.join("VRCForge.exe").display());
    if command.eq_ignore_ascii_case(&expected) {
        delete_key_if_present(&classes, VSK_PROGID)?;
        if let Ok(key) = classes.open_subkey_with_flags(r".vsk\OpenWithProgids", KEY_WRITE) {
            match key.delete_value(VSK_PROGID) { Ok(()) => {}, Err(error) if error.kind() == ErrorKind::NotFound => {}, Err(error) => return Err(format!("Could not remove .vsk Open With registration: {error}")), }
        }
    }
    let users = RegKey::predef(HKEY_USERS);
    delete_key_if_present(&users, &format!(r"{}\Software\Classes\AppUserModelId\app.vrcforge.agentic", user.sid))?;
    // DataDir remains available when the user keeps data, so reinstall can reuse it.
    if let Ok(key) = users.open_subkey_with_flags(format!(r"{}\{USER_KEY}", user.sid), KEY_WRITE) {
        match key.delete_value("InstallerLanguage") { Ok(()) => {}, Err(error) if error.kind() == ErrorKind::NotFound => {}, Err(error) => return Err(format!("Could not remove saved installer language: {error}")), }
    }
    // Keep the uninstall location available if any preceding cleanup fails,
    // so Apps & features can still reopen this uninstaller for a retry.
    delete_key_if_present(&machine, r"Microsoft\Windows\CurrentVersion\Uninstall\VRCForge")?;
    unsafe { SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None); }
    Ok(())
}

pub fn launch_app(user: &UserContext, directory: &Path) -> Result<(), String> {
    let executable = directory.join("VRCForge.exe");
    let app = wide(&executable); let work = wide(directory);
    let mut command = wide(format!("\"{}\"", executable.display()));
    unsafe {
        let mut environment = ptr::null_mut();
        CreateEnvironmentBlock(&mut environment, Some(user.token.get()), false)
            .map_err(|error| win_error("Could not create the logged-in user's App environment", error))?;
        let startup = STARTUPINFOW { cb: size_of::<STARTUPINFOW>() as u32, ..zeroed() };
        let mut child: PROCESS_INFORMATION = zeroed();
        let result = CreateProcessWithTokenW(user.token.get(), LOGON_WITH_PROFILE, PCWSTR(app.as_ptr()),
            Some(PWSTR(command.as_mut_ptr())), CREATE_UNICODE_ENVIRONMENT, Some(environment),
            PCWSTR(work.as_ptr()), &startup, &mut child);
        let _ = DestroyEnvironmentBlock(environment);
        result.map_err(|error| win_error("Could not start VRCForge as the logged-in user", error))?;
        // The App is user-owned after launch; setup does not wait or terminate it.
        let _ = CloseHandle(child.hThread); let _ = CloseHandle(child.hProcess);
    }
    Ok(())
}

pub fn choose_folder() -> Result<Option<PathBuf>, String> {
    // The native folder picker needs its own short-lived STA when called from
    // Tauri's async worker. It does not receive installation-write permissions.
    std::thread::spawn(|| {
        let _com = ComScope::new()?;
        unsafe {
            let dialog: IFileOpenDialog = CoCreateInstance(&FileOpenDialog, None, CLSCTX_INPROC_SERVER)
                .map_err(|error| win_error("Could not open the folder picker", error))?;
            dialog.SetOptions(dialog.GetOptions().map_err(|error| win_error("Could not read folder picker options", error))? | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM)
                .map_err(|error| win_error("Could not configure the folder picker", error))?;
            if let Err(error) = dialog.Show(None) {
                if error.code().0 == 0x800704c7u32 as i32 { return Ok(None); }
                return Err(win_error("The folder picker could not open", error));
            }
            let item = dialog.GetResult().map_err(|error| win_error("Could not read the selected folder", error))?;
            let text = item.GetDisplayName(SIGDN_FILESYSPATH).map_err(|error| win_error("Could not read the selected folder path", error))?;
            let result = text.to_string().map(PathBuf::from).map(Some)
                .map_err(|error| win_error("The selected folder path is invalid", error));
            CoTaskMemFree(Some(text.0 as _));
            result
        }
    }).join().map_err(|_| "The Windows folder picker stopped unexpectedly.".to_owned())?
}

fn installed_webview(root: &RegKey, path: &str, flags: u32) -> bool {
    root.open_subkey_with_flags(path, KEY_READ | flags)
        .and_then(|key| key.get_value::<String, _>("pv"))
        .is_ok_and(|version| !version.is_empty() && version != "0.0.0.0")
}

pub fn webview_ready() -> bool {
    if installed_webview(&RegKey::predef(HKEY_LOCAL_MACHINE), EDGE_CLIENT, KEY_WOW64_32KEY) { return true; }
    // WebView2 must be visible to this elevated setup process. Unlike App data,
    // the runtime's per-user registration belongs to the process account; when
    // only the desktop user has it, install the machine runtime before Tauri.
    installed_webview(&RegKey::predef(HKEY_CURRENT_USER), EDGE_CLIENT, KEY_WOW64_64KEY)
}

/// WebView2 must exist before the custom Tauri window can be created.
/// This is the user-approved startup dependency notice, not an install wizard.
/// Either choice exits setup; OK only opens Microsoft's official download page.
pub fn require_webview() -> Result<bool, String> {
    if webview_ready() { return Ok(true); }
    let text = match unsafe { GetUserDefaultUILanguage() } & 0x03ff {
        0x04 => "VRCForge 需要 Microsoft Edge WebView2 支持。\n\n点击“确定”打开微软官方下载页面，然后退出安装器。安装 WebView2 后，请重新运行安装器。\n点击“取消”直接退出。",
        0x11 => "VRCForge の実行には Microsoft Edge WebView2 が必要です。\n\n「OK」を押すと Microsoft の公式ダウンロードページを開き、インストーラーを終了します。WebView2 をインストールしてから、もう一度実行してください。\n「キャンセル」を押すと、そのまま終了します。",
        _ => "VRCForge requires Microsoft Edge WebView2.\n\nSelect OK to open Microsoft's official download page and exit setup. After installing WebView2, run setup again.\nSelect Cancel to exit without opening the page.",
    };
    let body = wide(text);
    let title = wide("VRCForge");
    let response = unsafe { MessageBoxW(None, PCWSTR(body.as_ptr()), PCWSTR(title.as_ptr()),
        MB_OKCANCEL | MB_ICONINFORMATION | MB_SETFOREGROUND) };
    if response == IDOK {
        tauri_plugin_opener::open_url("https://developer.microsoft.com/microsoft-edge/webview2/", None::<&str>)
            .map_err(|error| format!("Could not open the Microsoft WebView2 download page: {error}. Visit https://developer.microsoft.com/microsoft-edge/webview2/ and install WebView2 before running setup again."))?;
    }
    Ok(false)
}

struct RegistryTree { values: Vec<(String, RegValue)>, children: Vec<(String, RegistryTree)> }
struct SavedKey { machine: bool, path: String, tree: Option<RegistryTree> }
pub struct RegistrationSnapshot {
    keys: Vec<SavedKey>,
    shortcuts: Vec<(PathBuf, Option<Vec<u8>>)>,
    portable_data_permissions: Option<(PathBuf, SavedDacl)>,
}

fn read_tree(key: &RegKey) -> Result<RegistryTree, String> {
    let values = key.enum_values().collect::<Result<Vec<_>, _>>().map_err(|error| format!("Could not save existing install registration: {error}"))?;
    let mut children = Vec::new();
    for name in key.enum_keys() {
        let name = name.map_err(|error| format!("Could not save existing install subkeys: {error}"))?;
        let child = key.open_subkey_with_flags(&name, KEY_READ | KEY_WOW64_64KEY).map_err(|error| format!("Could not read existing install subkey '{name}': {error}"))?;
        children.push((name, read_tree(&child)?));
    }
    Ok(RegistryTree { values, children })
}

pub fn snapshot_registration(user: &UserContext, directory: &Path) -> Result<RegistrationSnapshot, String> {
    // Only setup's concrete registry keys and shortcut files are kept in memory
    // until finalization succeeds. No persistent journal or identity digest.
    let entries = [
        (true, UNINSTALL_KEY.to_owned()),
        (true, format!(r"Software\Classes\{VSK_PROGID}")),
        (true, r"Software\Classes\.vsk".to_owned()),
        (false, format!(r"{}\{USER_KEY}", user.sid)),
        (false, format!(r"{}\Software\Classes\AppUserModelId\app.vrcforge.agentic", user.sid)),
    ];
    let mut keys = Vec::new();
    for (machine, path) in entries {
        let root = RegKey::predef(if machine { HKEY_LOCAL_MACHINE } else { HKEY_USERS });
        let tree = match root.open_subkey_with_flags(&path, KEY_READ | KEY_WOW64_64KEY) {
            Ok(key) => Some(read_tree(&key)?),
            Err(error) if error.kind() == ErrorKind::NotFound => None,
            Err(error) => return Err(format!("Could not preserve registry key '{path}': {error}")),
        };
        keys.push(SavedKey { machine, path, tree });
    }
    let mut shortcuts = Vec::new();
    for path in shortcut_paths(user)? {
        let content = match fs::read(&path) {
            Ok(value) => Some(value),
            Err(error) if error.kind() == ErrorKind::NotFound => None,
            Err(error) => return Err(io_error("Could not preserve the current shortcut", &path, error)),
        };
        shortcuts.push((path, content));
    }
    let data = data_dir(user)?;
    let portable_data_permissions = if below_directory(&data, directory) && data.is_dir() {
        Some((data.clone(), SavedDacl::read(&data)?))
    } else { None };
    Ok(RegistrationSnapshot { keys, shortcuts, portable_data_permissions })
}

fn write_tree(key: &RegKey, tree: &RegistryTree) -> Result<(), String> {
    for (name, value) in &tree.values {
        key.set_raw_value(name, value).map_err(|error| format!("Could not restore registration value '{name}': {error}"))?;
    }
    for (name, child) in &tree.children {
        let (key, _) = key.create_subkey_with_flags(name, KEY_WRITE | KEY_WOW64_64KEY)
            .map_err(|error| format!("Could not restore registration subkey '{name}': {error}"))?;
        write_tree(&key, child)?;
    }
    Ok(())
}

pub fn restore_registration(_user: &UserContext, snapshot: &RegistrationSnapshot) -> Result<(), String> {
    let mut errors = Vec::new();
    if let Some((path, permissions)) = &snapshot.portable_data_permissions {
        // The engine restores original files/data before this call, so original
        // inherited permissions can be reinstated against the original parent.
        if let Err(error) = permissions.apply(path, permissions.protected) { errors.push(error); }
    }
    for saved in &snapshot.keys {
        let root = RegKey::predef(if saved.machine { HKEY_LOCAL_MACHINE } else { HKEY_USERS });
        let result = (|| {
            delete_key_if_present(&root, &saved.path)?;
            if let Some(tree) = &saved.tree {
                let (key, _) = root.create_subkey_with_flags(&saved.path, KEY_WRITE | KEY_WOW64_64KEY)
                    .map_err(|error| format!("Could not restore '{}': {error}", saved.path))?;
                write_tree(&key, tree)?;
            }
            Ok::<(), String>(())
        })();
        if let Err(error) = result { errors.push(error); }
    }
    for (path, content) in &snapshot.shortcuts {
        let result = match content {
            Some(bytes) => fs::create_dir_all(path.parent().unwrap()).and_then(|_| fs::write(path, bytes))
                .map_err(|error| io_error("Could not restore the previous shortcut", path, error)),
            None => remove_file_if_present(path),
        };
        if let Err(error) = result { errors.push(error); }
    }
    unsafe { SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None); }
    if errors.is_empty() { Ok(()) } else { Err(errors.join("\n")) }
}

use serde::Serialize;
use std::{
    fmt,
    path::{Component, Path, PathBuf},
};

pub const AUTHORITY_POLICY_SCHEMA: &str = "vrcforge.primitive_evidence_authority_policy.v1";
pub const AUTHORITY_READBACK_SCHEMA: &str = "vrcforge.primitive_evidence_authority_readback.v1";
pub const AUTHORITY_SERVICE_NAME: &str = "VRCForgePrimitiveEvidence";
pub const AUTHORITY_SERVICE_DISPLAY_NAME: &str = "VRCForge Primitive Evidence Authority";
pub const AUTHORITY_SERVICE_ACCOUNT: &str = "LocalSystem";
pub const AUTHORITY_PIPE_NAME: &str = r"\\.\pipe\VRCForge.PrimitiveEvidence.v1";
pub const AUTHORITY_PIPE_SDDL: &str = "O:SYG:SYD:P(A;;FA;;;SY)(A;;0x0012019b;;;BA)S:(ML;;NW;;;HI)";
pub const AUTHORITY_SERVICE_SID_TYPE_RESTRICTED: u32 = 3;
pub const AUTHORITY_REQUIRED_PRIVILEGES: [&str; 3] = [
    "SeAssignPrimaryTokenPrivilege",
    "SeIncreaseQuotaPrivilege",
    "SeTcbPrivilege",
];

const PERMANENT_BLOCKERS: [&str; 9] = [
    "authority_service_acl_not_verified",
    "authority_signing_key_not_verified",
    "authority_ledger_not_verified",
    "authority_controller_identity_not_verified",
    "isolated_runner_identity_not_implemented",
    "process_supervision_not_implemented",
    "private_finalization_not_implemented",
    "exclusive_port_handoff_not_implemented",
    "raw_full_projection_not_implemented",
];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthorityWindowsError(&'static str);

impl AuthorityWindowsError {
    pub fn code(&self) -> &'static str {
        self.0
    }
}

impl fmt::Display for AuthorityWindowsError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.0)
    }
}

impl std::error::Error for AuthorityWindowsError {}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct AuthorityLayout {
    binary_root: PathBuf,
    state_root: PathBuf,
    service_executable: PathBuf,
    install_helper_executable: PathBuf,
}

impl AuthorityLayout {
    fn from_roots(
        program_files: &Path,
        program_data: &Path,
    ) -> Result<Self, AuthorityWindowsError> {
        validate_absolute_root(program_files)?;
        validate_absolute_root(program_data)?;
        let binary_root = program_files
            .join("VRCForge")
            .join("EvidenceAuthority")
            .join("v1");
        let state_root = program_data
            .join("VRCForge")
            .join("EvidenceAuthority")
            .join("v1");
        Ok(Self {
            service_executable: binary_root.join("vrcforge_primitive_evidence_service.exe"),
            install_helper_executable: binary_root
                .join("vrcforge_primitive_evidence_install_helper.exe"),
            binary_root,
            state_root,
        })
    }

    pub(crate) fn installed() -> Result<Self, AuthorityWindowsError> {
        #[cfg(windows)]
        {
            let (program_files, program_data) = windows::known_folder_roots()?;
            Self::from_roots(&program_files, &program_data)
        }
        #[cfg(not(windows))]
        {
            Err(AuthorityWindowsError("authority_platform_unsupported"))
        }
    }

    #[cfg(test)]
    pub(crate) fn for_test_roots(
        program_files: &Path,
        program_data: &Path,
    ) -> Result<Self, AuthorityWindowsError> {
        Self::from_roots(program_files, program_data)
    }

    pub(crate) fn controller_executable_for_digest(
        &self,
        controller_sha256: &[u8; 32],
    ) -> Result<PathBuf, AuthorityWindowsError> {
        if controller_sha256.iter().all(|byte| *byte == 0) {
            return Err(AuthorityWindowsError("authority_controller_digest_invalid"));
        }
        Ok(self
            .binary_root
            .join(hex_lower(controller_sha256))
            .join("vrcforge_primitive_evidence_controller.exe"))
    }

    fn service_command(&self) -> Result<String, AuthorityWindowsError> {
        let path = self.service_executable.to_string_lossy();
        if path.contains('"') || path.is_empty() {
            return Err(AuthorityWindowsError("authority_service_path_invalid"));
        }
        Ok(format!("\"{path}\" --service"))
    }
}

fn hex_lower(value: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(value.len() * 2);
    for byte in value {
        output.push(DIGITS[(byte >> 4) as usize] as char);
        output.push(DIGITS[(byte & 0x0f) as usize] as char);
    }
    output
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
struct AuthorityLayoutProjection {
    binary_root: String,
    state_root: String,
    service_executable: String,
    controller_executable_pattern: String,
    install_helper_executable: String,
}

impl From<&AuthorityLayout> for AuthorityLayoutProjection {
    fn from(value: &AuthorityLayout) -> Self {
        Self {
            binary_root: value.binary_root.to_string_lossy().into_owned(),
            state_root: value.state_root.to_string_lossy().into_owned(),
            service_executable: value.service_executable.to_string_lossy().into_owned(),
            controller_executable_pattern: value
                .binary_root
                .join("{controller-sha256-lower}")
                .join("vrcforge_primitive_evidence_controller.exe")
                .to_string_lossy()
                .into_owned(),
            install_helper_executable: value
                .install_helper_executable
                .to_string_lossy()
                .into_owned(),
        }
    }
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub(crate) struct AuthorityInstallPlan {
    schema: &'static str,
    mutation_supported: bool,
    trusted_boundary_ready: bool,
    service_name: &'static str,
    service_display_name: &'static str,
    service_account: &'static str,
    service_start: &'static str,
    service_sid_type: &'static str,
    required_privileges: Vec<&'static str>,
    pipe_name: &'static str,
    pipe_security_sddl: &'static str,
    controller_path_policy: &'static str,
    candidate_payload_includes_authority: bool,
    layout: AuthorityLayoutProjection,
    blockers: Vec<&'static str>,
}

pub(crate) fn build_install_plan(layout: &AuthorityLayout) -> AuthorityInstallPlan {
    AuthorityInstallPlan {
        schema: AUTHORITY_POLICY_SCHEMA,
        mutation_supported: false,
        trusted_boundary_ready: false,
        service_name: AUTHORITY_SERVICE_NAME,
        service_display_name: AUTHORITY_SERVICE_DISPLAY_NAME,
        service_account: AUTHORITY_SERVICE_ACCOUNT,
        service_start: "demand",
        service_sid_type: "restricted",
        required_privileges: AUTHORITY_REQUIRED_PRIVILEGES.to_vec(),
        pipe_name: AUTHORITY_PIPE_NAME,
        pipe_security_sddl: AUTHORITY_PIPE_SDDL,
        controller_path_policy: "sha256-parent-create-new-never-reuse",
        candidate_payload_includes_authority: false,
        layout: AuthorityLayoutProjection::from(layout),
        blockers: PERMANENT_BLOCKERS.to_vec(),
    }
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct AuthorityReadback {
    schema: &'static str,
    trusted_boundary_ready: bool,
    service_installed: bool,
    service_running: bool,
    service_binary_path_exact: bool,
    service_account_exact: bool,
    service_type_exact: bool,
    service_start_exact: bool,
    service_error_control_exact: bool,
    service_sid_restricted: bool,
    required_privileges_exact: bool,
    blockers: Vec<String>,
}

impl AuthorityReadback {
    fn absent(code: &str) -> Self {
        let mut blockers = vec![code.to_string()];
        blockers.extend(PERMANENT_BLOCKERS.into_iter().map(str::to_string));
        Self {
            schema: AUTHORITY_READBACK_SCHEMA,
            trusted_boundary_ready: false,
            service_installed: false,
            service_running: false,
            service_binary_path_exact: false,
            service_account_exact: false,
            service_type_exact: false,
            service_start_exact: false,
            service_error_control_exact: false,
            service_sid_restricted: false,
            required_privileges_exact: false,
            blockers,
        }
    }

    fn push_if_false(&mut self, value: bool, code: &'static str) {
        if !value {
            self.blockers.push(code.to_string());
        }
    }
}

#[cfg(windows)]
pub fn inspect_installed_authority(
    layout: &AuthorityLayout,
) -> Result<AuthorityReadback, AuthorityWindowsError> {
    windows::inspect(layout)
}

#[cfg(not(windows))]
pub fn inspect_installed_authority(
    _layout: &AuthorityLayout,
) -> Result<AuthorityReadback, AuthorityWindowsError> {
    Ok(AuthorityReadback::absent("authority_platform_unsupported"))
}

fn validate_absolute_root(path: &Path) -> Result<(), AuthorityWindowsError> {
    if !path.is_absolute()
        || path.as_os_str().is_empty()
        || path
            .components()
            .any(|component| matches!(component, Component::CurDir | Component::ParentDir))
    {
        return Err(AuthorityWindowsError("authority_layout_root_invalid"));
    }
    Ok(())
}

#[cfg(windows)]
mod windows {
    use super::*;
    use std::{
        ffi::OsString,
        mem::{size_of, MaybeUninit},
        os::windows::ffi::OsStringExt,
        ptr,
    };
    use windows_sys::Win32::{
        Foundation::{GetLastError, ERROR_INSUFFICIENT_BUFFER, ERROR_SERVICE_DOES_NOT_EXIST},
        System::Com::CoTaskMemFree,
        System::Services::{
            CloseServiceHandle, OpenSCManagerW, OpenServiceW, QueryServiceConfig2W,
            QueryServiceConfigW, QueryServiceStatusEx, QUERY_SERVICE_CONFIGW, SC_HANDLE,
            SC_MANAGER_CONNECT, SC_STATUS_PROCESS_INFO, SERVICE_CONFIG_REQUIRED_PRIVILEGES_INFO,
            SERVICE_CONFIG_SERVICE_SID_INFO, SERVICE_DEMAND_START, SERVICE_ERROR_NORMAL,
            SERVICE_QUERY_CONFIG, SERVICE_QUERY_STATUS, SERVICE_REQUIRED_PRIVILEGES_INFOW,
            SERVICE_RUNNING, SERVICE_SID_INFO, SERVICE_STATUS_PROCESS, SERVICE_WIN32_OWN_PROCESS,
        },
        UI::Shell::{FOLDERID_ProgramData, FOLDERID_ProgramFiles, SHGetKnownFolderPath},
    };

    const READ_CONTROL_ACCESS: u32 = 0x0002_0000;

    pub(super) fn known_folder_roots() -> Result<(PathBuf, PathBuf), AuthorityWindowsError> {
        Ok((
            known_folder_path(&FOLDERID_ProgramFiles, "program_files_unavailable")?,
            known_folder_path(&FOLDERID_ProgramData, "program_data_unavailable")?,
        ))
    }

    fn known_folder_path(
        folder_id: *const windows_sys::core::GUID,
        unavailable_code: &'static str,
    ) -> Result<PathBuf, AuthorityWindowsError> {
        let mut raw = ptr::null_mut::<u16>();
        let result = unsafe { SHGetKnownFolderPath(folder_id, 0, ptr::null_mut(), &mut raw) };
        if result < 0 || raw.is_null() {
            if !raw.is_null() {
                unsafe {
                    CoTaskMemFree(raw.cast());
                }
            }
            return Err(AuthorityWindowsError(unavailable_code));
        }
        let mut length = 0usize;
        while length < 32_768 && unsafe { *raw.add(length) } != 0 {
            length += 1;
        }
        let value = if length == 0 || length == 32_768 {
            None
        } else {
            let slice = unsafe { std::slice::from_raw_parts(raw, length) };
            Some(PathBuf::from(OsString::from_wide(slice)))
        };
        unsafe {
            CoTaskMemFree(raw.cast());
        }
        value.ok_or(AuthorityWindowsError(unavailable_code))
    }

    struct ServiceHandle(SC_HANDLE);

    impl Drop for ServiceHandle {
        fn drop(&mut self) {
            if !self.0.is_null() {
                unsafe {
                    CloseServiceHandle(self.0);
                }
            }
        }
    }

    struct AlignedBuffer {
        words: Vec<usize>,
        byte_len: usize,
    }

    impl AlignedBuffer {
        fn new(byte_len: u32) -> Result<Self, AuthorityWindowsError> {
            let byte_len = usize::try_from(byte_len)
                .map_err(|_| AuthorityWindowsError("authority_service_config_too_large"))?;
            if byte_len == 0 || byte_len > 1024 * 1024 {
                return Err(AuthorityWindowsError(
                    "authority_service_config_size_invalid",
                ));
            }
            let word_size = size_of::<usize>();
            let word_count = byte_len
                .checked_add(word_size - 1)
                .ok_or(AuthorityWindowsError("authority_service_config_too_large"))?
                / word_size;
            Ok(Self {
                words: vec![0usize; word_count],
                byte_len,
            })
        }

        fn as_mut_u8(&mut self) -> *mut u8 {
            self.words.as_mut_ptr().cast::<u8>()
        }

        fn contains_wide_ptr(&self, value: *const u16) -> bool {
            let start = self.words.as_ptr() as usize;
            let end = start.saturating_add(self.byte_len);
            let pointer = value as usize;
            pointer >= start && pointer < end && pointer % std::mem::align_of::<u16>() == 0
        }
    }

    pub(super) fn inspect(
        layout: &AuthorityLayout,
    ) -> Result<AuthorityReadback, AuthorityWindowsError> {
        let manager =
            ServiceHandle(unsafe { OpenSCManagerW(ptr::null(), ptr::null(), SC_MANAGER_CONNECT) });
        if manager.0.is_null() {
            return Ok(AuthorityReadback::absent(
                "authority_scm_readback_unavailable",
            ));
        }
        let service_name = wide_null(AUTHORITY_SERVICE_NAME);
        let service = ServiceHandle(unsafe {
            OpenServiceW(
                manager.0,
                service_name.as_ptr(),
                SERVICE_QUERY_CONFIG | SERVICE_QUERY_STATUS | READ_CONTROL_ACCESS,
            )
        });
        if service.0.is_null() {
            let error = unsafe { GetLastError() };
            if error == ERROR_SERVICE_DOES_NOT_EXIST {
                return Ok(AuthorityReadback::absent("authority_service_not_installed"));
            }
            return Ok(AuthorityReadback::absent(
                "authority_service_readback_denied",
            ));
        }

        let (config, config_buffer) = query_primary_config(service.0)?;
        let binary_path = wide_string_in_buffer(config.lpBinaryPathName, &config_buffer)?;
        let account = wide_string_in_buffer(config.lpServiceStartName, &config_buffer)?;
        let sid =
            query_fixed_config::<SERVICE_SID_INFO>(service.0, SERVICE_CONFIG_SERVICE_SID_INFO)?;
        let privileges = query_required_privileges(service.0)?;
        let mut status = MaybeUninit::<SERVICE_STATUS_PROCESS>::zeroed();
        let mut required = 0u32;
        let status_ok = unsafe {
            QueryServiceStatusEx(
                service.0,
                SC_STATUS_PROCESS_INFO,
                status.as_mut_ptr().cast::<u8>(),
                size_of::<SERVICE_STATUS_PROCESS>() as u32,
                &mut required,
            )
        } != 0;
        let running =
            status_ok && unsafe { status.assume_init() }.dwCurrentState == SERVICE_RUNNING;

        let service_binary_path_exact =
            binary_path.eq_ignore_ascii_case(&layout.service_command()?);
        let service_account_exact = account.eq_ignore_ascii_case(AUTHORITY_SERVICE_ACCOUNT);
        let service_type_exact = config.dwServiceType == SERVICE_WIN32_OWN_PROCESS;
        let service_start_exact = config.dwStartType == SERVICE_DEMAND_START;
        let service_error_control_exact = config.dwErrorControl == SERVICE_ERROR_NORMAL;
        let service_sid_restricted = sid.dwServiceSidType == AUTHORITY_SERVICE_SID_TYPE_RESTRICTED;
        let required_privileges_exact = privileges == AUTHORITY_REQUIRED_PRIVILEGES;
        let mut readback = AuthorityReadback {
            schema: AUTHORITY_READBACK_SCHEMA,
            trusted_boundary_ready: false,
            service_installed: true,
            service_running: running,
            service_binary_path_exact,
            service_account_exact,
            service_type_exact,
            service_start_exact,
            service_error_control_exact,
            service_sid_restricted,
            required_privileges_exact,
            blockers: PERMANENT_BLOCKERS.into_iter().map(str::to_string).collect(),
        };
        readback.push_if_false(
            service_binary_path_exact,
            "authority_service_binary_path_mismatch",
        );
        readback.push_if_false(service_account_exact, "authority_service_account_mismatch");
        readback.push_if_false(service_type_exact, "authority_service_type_mismatch");
        readback.push_if_false(service_start_exact, "authority_service_start_mismatch");
        readback.push_if_false(
            service_error_control_exact,
            "authority_service_error_control_mismatch",
        );
        readback.push_if_false(service_sid_restricted, "authority_service_sid_mismatch");
        readback.push_if_false(
            required_privileges_exact,
            "authority_service_privilege_set_mismatch",
        );
        readback.push_if_false(status_ok, "authority_service_status_unavailable");
        Ok(readback)
    }

    fn query_primary_config(
        service: SC_HANDLE,
    ) -> Result<(QUERY_SERVICE_CONFIGW, AlignedBuffer), AuthorityWindowsError> {
        let mut required = 0u32;
        unsafe {
            QueryServiceConfigW(service, ptr::null_mut(), 0, &mut required);
        }
        if required == 0 || unsafe { GetLastError() } != ERROR_INSUFFICIENT_BUFFER {
            return Err(AuthorityWindowsError(
                "authority_service_config_unavailable",
            ));
        }
        let mut buffer = AlignedBuffer::new(required)?;
        if unsafe {
            QueryServiceConfigW(
                service,
                buffer.as_mut_u8().cast::<QUERY_SERVICE_CONFIGW>(),
                required,
                &mut required,
            )
        } == 0
        {
            return Err(AuthorityWindowsError(
                "authority_service_config_unavailable",
            ));
        }
        let value = unsafe { *(buffer.words.as_ptr().cast::<QUERY_SERVICE_CONFIGW>()) };
        Ok((value, buffer))
    }

    fn query_fixed_config<T: Copy>(
        service: SC_HANDLE,
        level: u32,
    ) -> Result<T, AuthorityWindowsError> {
        let mut value = MaybeUninit::<T>::zeroed();
        let mut required = 0u32;
        if unsafe {
            QueryServiceConfig2W(
                service,
                level,
                value.as_mut_ptr().cast::<u8>(),
                size_of::<T>() as u32,
                &mut required,
            )
        } == 0
        {
            return Err(AuthorityWindowsError(
                "authority_service_extended_config_unavailable",
            ));
        }
        Ok(unsafe { value.assume_init() })
    }

    fn query_required_privileges(service: SC_HANDLE) -> Result<[String; 3], AuthorityWindowsError> {
        let mut required = 0u32;
        unsafe {
            QueryServiceConfig2W(
                service,
                SERVICE_CONFIG_REQUIRED_PRIVILEGES_INFO,
                ptr::null_mut(),
                0,
                &mut required,
            );
        }
        if required == 0 || unsafe { GetLastError() } != ERROR_INSUFFICIENT_BUFFER {
            return Err(AuthorityWindowsError(
                "authority_service_privileges_unavailable",
            ));
        }
        let mut buffer = AlignedBuffer::new(required)?;
        if unsafe {
            QueryServiceConfig2W(
                service,
                SERVICE_CONFIG_REQUIRED_PRIVILEGES_INFO,
                buffer.as_mut_u8(),
                required,
                &mut required,
            )
        } == 0
        {
            return Err(AuthorityWindowsError(
                "authority_service_privileges_unavailable",
            ));
        }
        let info = unsafe {
            *(buffer
                .words
                .as_ptr()
                .cast::<SERVICE_REQUIRED_PRIVILEGES_INFOW>())
        };
        let mut values = wide_multi_string_in_buffer(info.pmszRequiredPrivileges, &buffer)?;
        values.sort();
        if values.len() != AUTHORITY_REQUIRED_PRIVILEGES.len() {
            return Ok([String::new(), String::new(), String::new()]);
        }
        Ok([values.remove(0), values.remove(0), values.remove(0)])
    }

    fn wide_string_in_buffer(
        pointer: *const u16,
        buffer: &AlignedBuffer,
    ) -> Result<String, AuthorityWindowsError> {
        if pointer.is_null() || !buffer.contains_wide_ptr(pointer) {
            return Err(AuthorityWindowsError("authority_service_string_invalid"));
        }
        let max_units = ((buffer.words.as_ptr() as usize + buffer.byte_len) - pointer as usize) / 2;
        let mut length = 0usize;
        while length < max_units && unsafe { *pointer.add(length) } != 0 {
            length += 1;
        }
        if length == max_units {
            return Err(AuthorityWindowsError("authority_service_string_invalid"));
        }
        String::from_utf16(unsafe { std::slice::from_raw_parts(pointer, length) })
            .map_err(|_| AuthorityWindowsError("authority_service_string_invalid"))
    }

    fn wide_multi_string_in_buffer(
        pointer: *const u16,
        buffer: &AlignedBuffer,
    ) -> Result<Vec<String>, AuthorityWindowsError> {
        if pointer.is_null() || !buffer.contains_wide_ptr(pointer) {
            return Err(AuthorityWindowsError(
                "authority_service_privileges_invalid",
            ));
        }
        let max_units = ((buffer.words.as_ptr() as usize + buffer.byte_len) - pointer as usize) / 2;
        let mut values = Vec::new();
        let mut offset = 0usize;
        loop {
            if offset >= max_units {
                return Err(AuthorityWindowsError(
                    "authority_service_privileges_invalid",
                ));
            }
            let start = offset;
            while offset < max_units && unsafe { *pointer.add(offset) } != 0 {
                offset += 1;
            }
            if offset == max_units {
                return Err(AuthorityWindowsError(
                    "authority_service_privileges_invalid",
                ));
            }
            if offset == start {
                break;
            }
            values.push(
                String::from_utf16(unsafe {
                    std::slice::from_raw_parts(pointer.add(start), offset - start)
                })
                .map_err(|_| AuthorityWindowsError("authority_service_privileges_invalid"))?,
            );
            offset += 1;
        }
        Ok(values)
    }

    fn wide_null(value: &str) -> Vec<u16> {
        value.encode_utf16().chain(std::iter::once(0)).collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn layout_is_fixed_below_machine_roots() {
        let layout = AuthorityLayout::from_roots(
            Path::new(r"C:\Program Files"),
            Path::new(r"C:\ProgramData"),
        )
        .expect("absolute machine roots should be accepted");
        assert_eq!(
            layout.service_executable,
            PathBuf::from(
                r"C:\Program Files\VRCForge\EvidenceAuthority\v1\vrcforge_primitive_evidence_service.exe"
            )
        );
        assert_eq!(
            layout.state_root,
            PathBuf::from(r"C:\ProgramData\VRCForge\EvidenceAuthority\v1")
        );
        assert_eq!(
            layout.service_command().unwrap(),
            r#""C:\Program Files\VRCForge\EvidenceAuthority\v1\vrcforge_primitive_evidence_service.exe" --service"#
        );
        assert_eq!(
            layout
                .controller_executable_for_digest(&[0x42; 32])
                .unwrap(),
            PathBuf::from(format!(
                r"C:\Program Files\VRCForge\EvidenceAuthority\v1\{}\vrcforge_primitive_evidence_controller.exe",
                "42".repeat(32)
            ))
        );
        assert_eq!(
            layout
                .controller_executable_for_digest(&[0; 32])
                .unwrap_err()
                .code(),
            "authority_controller_digest_invalid"
        );
    }

    #[test]
    fn layout_rejects_relative_and_traversing_roots() {
        assert_eq!(
            AuthorityLayout::from_roots(Path::new("Program Files"), Path::new(r"C:\ProgramData"))
                .unwrap_err()
                .code(),
            "authority_layout_root_invalid"
        );
        assert_eq!(
            AuthorityLayout::from_roots(
                Path::new(r"C:\safe\..\Program Files"),
                Path::new(r"C:\ProgramData")
            )
            .unwrap_err()
            .code(),
            "authority_layout_root_invalid"
        );
    }

    #[test]
    fn plan_is_non_mutating_and_never_ready() {
        let layout = AuthorityLayout::from_roots(
            Path::new(r"C:\Program Files"),
            Path::new(r"C:\ProgramData"),
        )
        .unwrap();
        let value = serde_json::to_value(build_install_plan(&layout)).unwrap();
        assert_eq!(value["mutationSupported"], false);
        assert_eq!(value["trustedBoundaryReady"], false);
        assert_eq!(value["candidatePayloadIncludesAuthority"], false);
        assert_eq!(value["serviceStart"], "demand");
        assert_eq!(value["serviceSidType"], "restricted");
        assert_eq!(
            value["controllerPathPolicy"],
            "sha256-parent-create-new-never-reuse"
        );
        assert_eq!(
            value["layout"]["controllerExecutablePattern"],
            r"C:\Program Files\VRCForge\EvidenceAuthority\v1\{controller-sha256-lower}\vrcforge_primitive_evidence_controller.exe"
        );
        assert!(value["layout"].get("controllerExecutable").is_none());
        assert!(value["blockers"].as_array().unwrap().len() >= 9);
    }

    #[test]
    fn pipe_policy_excludes_unprivileged_principals_and_create_instance_access() {
        assert!(AUTHORITY_PIPE_SDDL.contains(";;;SY"));
        assert!(AUTHORITY_PIPE_SDDL.contains(";;;BA"));
        assert!(AUTHORITY_PIPE_SDDL.contains(";;;HI"));
        for forbidden in [";;;WD", ";;;AU", ";;;BU", "(A;;GA;;;BA)"] {
            assert!(!AUTHORITY_PIPE_SDDL.contains(forbidden));
        }
        assert!(AUTHORITY_PIPE_SDDL.contains("0x0012019b"));
    }

    #[test]
    fn privilege_set_is_exact_and_stable() {
        assert_eq!(
            AUTHORITY_REQUIRED_PRIVILEGES,
            [
                "SeAssignPrimaryTokenPrivilege",
                "SeIncreaseQuotaPrivilege",
                "SeTcbPrivilege",
            ]
        );
    }
}

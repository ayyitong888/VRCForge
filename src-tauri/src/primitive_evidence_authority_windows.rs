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
pub const AUTHORITY_SERVICE_SECURITY_SDDL: &str =
    "O:SYG:SYD:P(A;;FA;;;SY)(A;;0x000f01ff;;;BA)S:(ML;;NW;;;HI)";
pub const AUTHORITY_SERVICE_SID_TYPE_RESTRICTED: u32 = 3;
pub const AUTHORITY_REQUIRED_PRIVILEGES: [&str; 3] = [
    "SeAssignPrimaryTokenPrivilege",
    "SeIncreaseQuotaPrivilege",
    "SeTcbPrivilege",
];

const PERMANENT_BLOCKERS: [&str; 18] = [
    "authority_service_dacl_not_verified",
    "authority_service_running_process_not_verified",
    "authority_service_running_image_not_verified",
    "authority_service_generation_handshake_not_verified",
    "authority_binary_anchor_chain_not_verified",
    "authority_state_anchor_chain_not_verified",
    "authority_generation_payloads_not_verified",
    "authority_signing_key_not_verified",
    "authority_ledger_not_verified",
    "authority_trust_manifest_not_verified",
    "authority_activation_manifest_not_verified",
    "authority_retirement_state_not_verified",
    "authority_recovery_state_not_verified",
    "authority_controller_identity_not_verified",
    "authority_controller_launch_receipt_not_verified",
    "isolated_runner_identity_not_implemented",
    "process_supervision_not_implemented",
    "private_finalization_not_implemented",
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
    binary_anchor: PathBuf,
    state_anchor: PathBuf,
    binary_base: PathBuf,
    state_base: PathBuf,
    binary_root: PathBuf,
    state_root: PathBuf,
}

impl AuthorityLayout {
    fn from_roots(
        program_files: &Path,
        program_data: &Path,
    ) -> Result<Self, AuthorityWindowsError> {
        validate_absolute_root(program_files)?;
        validate_absolute_root(program_data)?;
        let binary_anchor = program_files.to_path_buf();
        let state_anchor = program_data.to_path_buf();
        let binary_base = binary_anchor.join("VRCForgeEvidenceAuthority");
        let state_base = state_anchor.join("VRCForgeEvidenceAuthority");
        let binary_root = binary_base.join("v1");
        let state_root = state_base.join("v1");
        Ok(Self {
            binary_anchor,
            state_anchor,
            binary_base,
            state_base,
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

    pub(crate) fn binary_anchor(&self) -> &Path {
        &self.binary_anchor
    }

    pub(crate) fn state_anchor(&self) -> &Path {
        &self.state_anchor
    }

    pub(crate) fn binary_base(&self) -> &Path {
        &self.binary_base
    }

    pub(crate) fn state_base(&self) -> &Path {
        &self.state_base
    }

    pub(crate) fn binary_root(&self) -> &Path {
        &self.binary_root
    }

    pub(crate) fn state_root(&self) -> &Path {
        &self.state_root
    }

    pub(crate) fn generation_binary_root(
        &self,
        authority_generation_sha256: &[u8; 32],
    ) -> Result<PathBuf, AuthorityWindowsError> {
        require_nonzero_digest(
            authority_generation_sha256,
            "authority_generation_digest_invalid",
        )?;
        Ok(self
            .binary_root
            .join("generations")
            .join(hex_lower(authority_generation_sha256)))
    }

    pub(crate) fn generation_state_root(
        &self,
        authority_generation_sha256: &[u8; 32],
    ) -> Result<PathBuf, AuthorityWindowsError> {
        require_nonzero_digest(
            authority_generation_sha256,
            "authority_generation_digest_invalid",
        )?;
        Ok(self
            .state_root
            .join("generations")
            .join(hex_lower(authority_generation_sha256)))
    }

    pub(crate) fn controller_executable_for_generation(
        &self,
        authority_generation_sha256: &[u8; 32],
    ) -> Result<PathBuf, AuthorityWindowsError> {
        Ok(self
            .generation_binary_root(authority_generation_sha256)?
            .join("vrcforge_primitive_evidence_controller.exe"))
    }

    pub(crate) fn service_executable_for_generation(
        &self,
        authority_generation_sha256: &[u8; 32],
    ) -> Result<PathBuf, AuthorityWindowsError> {
        Ok(self
            .generation_binary_root(authority_generation_sha256)?
            .join("vrcforge_primitive_evidence_service.exe"))
    }

    pub(crate) fn install_helper_executable_for_generation(
        &self,
        authority_generation_sha256: &[u8; 32],
    ) -> Result<PathBuf, AuthorityWindowsError> {
        Ok(self
            .generation_binary_root(authority_generation_sha256)?
            .join("vrcforge_primitive_evidence_install_helper.exe"))
    }

    fn service_command_for_generation(
        &self,
        authority_generation_sha256: &[u8; 32],
    ) -> Result<String, AuthorityWindowsError> {
        let executable = self.service_executable_for_generation(authority_generation_sha256)?;
        let path = executable.to_string_lossy();
        if path.contains('"') || path.is_empty() {
            return Err(AuthorityWindowsError("authority_service_path_invalid"));
        }
        Ok(format!("\"{path}\" --service"))
    }
}

fn require_nonzero_digest(
    value: &[u8; 32],
    code: &'static str,
) -> Result<(), AuthorityWindowsError> {
    if value.iter().all(|byte| *byte == 0) {
        Err(AuthorityWindowsError(code))
    } else {
        Ok(())
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
    binary_anchor: String,
    state_anchor: String,
    binary_base: String,
    state_base: String,
    binary_version_root: String,
    state_version_root: String,
    generation_binary_root_pattern: String,
    generation_state_root_pattern: String,
    service_executable_pattern: String,
    controller_executable_pattern: String,
    install_helper_executable_pattern: String,
}

impl From<&AuthorityLayout> for AuthorityLayoutProjection {
    fn from(value: &AuthorityLayout) -> Self {
        Self {
            binary_anchor: value.binary_anchor.to_string_lossy().into_owned(),
            state_anchor: value.state_anchor.to_string_lossy().into_owned(),
            binary_base: value.binary_base.to_string_lossy().into_owned(),
            state_base: value.state_base.to_string_lossy().into_owned(),
            binary_version_root: value.binary_root.to_string_lossy().into_owned(),
            state_version_root: value.state_root.to_string_lossy().into_owned(),
            generation_binary_root_pattern: value
                .binary_root
                .join("generations")
                .join("{authority-generation-sha256-lower}")
                .to_string_lossy()
                .into_owned(),
            generation_state_root_pattern: value
                .state_root
                .join("generations")
                .join("{authority-generation-sha256-lower}")
                .to_string_lossy()
                .into_owned(),
            service_executable_pattern: value
                .binary_root
                .join("generations")
                .join("{authority-generation-sha256-lower}")
                .join("vrcforge_primitive_evidence_service.exe")
                .to_string_lossy()
                .into_owned(),
            controller_executable_pattern: value
                .binary_root
                .join("generations")
                .join("{authority-generation-sha256-lower}")
                .join("vrcforge_primitive_evidence_controller.exe")
                .to_string_lossy()
                .into_owned(),
            install_helper_executable_pattern: value
                .binary_root
                .join("generations")
                .join("{authority-generation-sha256-lower}")
                .join("vrcforge_primitive_evidence_install_helper.exe")
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
    service_security_sddl: &'static str,
    service_sid_type: &'static str,
    required_privileges: Vec<&'static str>,
    pipe_name: &'static str,
    pipe_security_sddl: &'static str,
    generation_path_policy: &'static str,
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
        service_security_sddl: AUTHORITY_SERVICE_SECURITY_SDDL,
        service_sid_type: "restricted",
        required_privileges: AUTHORITY_REQUIRED_PRIVILEGES.to_vec(),
        pipe_name: AUTHORITY_PIPE_NAME,
        pipe_security_sddl: AUTHORITY_PIPE_SDDL,
        generation_path_policy: "authority-generation-sha256-parent-create-new-never-reuse",
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
    expected_generation: Option<String>,
    generation_bound: bool,
    diagnostic_only: bool,
    service_installed: bool,
    service_running: bool,
    service_binary_path_exact: bool,
    service_executable_path_exact: bool,
    service_arguments_exact: bool,
    service_account_exact: bool,
    service_type_exact: bool,
    service_start_exact: bool,
    service_error_control_exact: bool,
    service_dacl_exact: bool,
    service_sid_restricted: bool,
    required_privileges_exact: bool,
    running_process_id: Option<u32>,
    running_process_creation_time: Option<u64>,
    running_process_identity_exact: bool,
    running_image_path_exact: bool,
    running_image_file_identity_exact: bool,
    running_image_sha256_exact: bool,
    running_image_generation_handshake_exact: bool,
    controller_launch_receipt_exact: bool,
    binary_anchor_chain_exact: bool,
    state_anchor_chain_exact: bool,
    generation_payloads_exact: bool,
    signing_key_exact: bool,
    ledger_exact: bool,
    trust_manifest_exact: bool,
    activation_manifest_exact: bool,
    retirement_state_exact: bool,
    recovery_state_exact: bool,
    protected_readback_complete: bool,
    blockers: Vec<String>,
}

impl AuthorityReadback {
    fn absent(code: &str, expected_generation: Option<&[u8; 32]>) -> Self {
        let mut blockers = vec![code.to_string()];
        if expected_generation.is_none() {
            blockers.push("authority_generation_required_for_readback".to_string());
        }
        blockers.extend(PERMANENT_BLOCKERS.into_iter().map(str::to_string));
        Self {
            schema: AUTHORITY_READBACK_SCHEMA,
            trusted_boundary_ready: false,
            expected_generation: expected_generation.map(|value| hex_lower(value)),
            generation_bound: expected_generation.is_some(),
            diagnostic_only: expected_generation.is_none(),
            service_installed: false,
            service_running: false,
            service_binary_path_exact: false,
            service_executable_path_exact: false,
            service_arguments_exact: false,
            service_account_exact: false,
            service_type_exact: false,
            service_start_exact: false,
            service_error_control_exact: false,
            service_dacl_exact: false,
            service_sid_restricted: false,
            required_privileges_exact: false,
            running_process_id: None,
            running_process_creation_time: None,
            running_process_identity_exact: false,
            running_image_path_exact: false,
            running_image_file_identity_exact: false,
            running_image_sha256_exact: false,
            running_image_generation_handshake_exact: false,
            controller_launch_receipt_exact: false,
            binary_anchor_chain_exact: false,
            state_anchor_chain_exact: false,
            generation_payloads_exact: false,
            signing_key_exact: false,
            ledger_exact: false,
            trust_manifest_exact: false,
            activation_manifest_exact: false,
            retirement_state_exact: false,
            recovery_state_exact: false,
            protected_readback_complete: false,
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
    windows::inspect(layout, None)
}

#[cfg(not(windows))]
pub fn inspect_installed_authority(
    _layout: &AuthorityLayout,
) -> Result<AuthorityReadback, AuthorityWindowsError> {
    Ok(AuthorityReadback::absent(
        "authority_platform_unsupported",
        None,
    ))
}

#[cfg(windows)]
pub fn inspect_installed_authority_for_generation(
    layout: &AuthorityLayout,
    authority_generation_sha256: &[u8; 32],
) -> Result<AuthorityReadback, AuthorityWindowsError> {
    require_nonzero_digest(
        authority_generation_sha256,
        "authority_generation_digest_invalid",
    )?;
    windows::inspect(layout, Some(authority_generation_sha256))
}

#[cfg(not(windows))]
pub fn inspect_installed_authority_for_generation(
    _layout: &AuthorityLayout,
    authority_generation_sha256: &[u8; 32],
) -> Result<AuthorityReadback, AuthorityWindowsError> {
    require_nonzero_digest(
        authority_generation_sha256,
        "authority_generation_digest_invalid",
    )?;
    Ok(AuthorityReadback::absent(
        "authority_platform_unsupported",
        Some(authority_generation_sha256),
    ))
}

fn compare_service_command(actual: &str, expected_executable: &Path) -> (bool, bool) {
    let Some(after_open_quote) = actual.strip_prefix('"') else {
        return (false, false);
    };
    let Some(close_quote) = after_open_quote.find('"') else {
        return (false, false);
    };
    let executable = &after_open_quote[..close_quote];
    let arguments = &after_open_quote[close_quote + 1..];
    (
        executable.eq_ignore_ascii_case(&expected_executable.to_string_lossy()),
        arguments == " --service",
    )
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
        expected_generation: Option<&[u8; 32]>,
    ) -> Result<AuthorityReadback, AuthorityWindowsError> {
        let manager =
            ServiceHandle(unsafe { OpenSCManagerW(ptr::null(), ptr::null(), SC_MANAGER_CONNECT) });
        if manager.0.is_null() {
            return Ok(AuthorityReadback::absent(
                "authority_scm_readback_unavailable",
                expected_generation,
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
                return Ok(AuthorityReadback::absent(
                    "authority_service_not_installed",
                    expected_generation,
                ));
            }
            return Ok(AuthorityReadback::absent(
                "authority_service_readback_denied",
                expected_generation,
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
        let status = status_ok.then(|| unsafe { status.assume_init() });
        let running = status
            .as_ref()
            .is_some_and(|value| value.dwCurrentState == SERVICE_RUNNING);
        let running_process_id = status
            .as_ref()
            .filter(|value| value.dwCurrentState == SERVICE_RUNNING && value.dwProcessId != 0)
            .map(|value| value.dwProcessId);

        let (service_executable_path_exact, service_arguments_exact) = match expected_generation {
            Some(generation) => compare_service_command(
                &binary_path,
                &layout.service_executable_for_generation(generation)?,
            ),
            None => (false, false),
        };
        let service_binary_path_exact = service_executable_path_exact && service_arguments_exact;
        let service_account_exact = account.eq_ignore_ascii_case(AUTHORITY_SERVICE_ACCOUNT);
        let service_type_exact = config.dwServiceType == SERVICE_WIN32_OWN_PROCESS;
        let service_start_exact = config.dwStartType == SERVICE_DEMAND_START;
        let service_error_control_exact = config.dwErrorControl == SERVICE_ERROR_NORMAL;
        // The source checkpoint intentionally has no service-object DACL or
        // running-image verifier. These fields remain false until readback is
        // derived from held service/process/image handles and a launch receipt.
        let service_dacl_exact = false;
        let service_sid_restricted = sid.dwServiceSidType == AUTHORITY_SERVICE_SID_TYPE_RESTRICTED;
        let required_privileges_exact = privileges == AUTHORITY_REQUIRED_PRIVILEGES;
        let mut readback = AuthorityReadback {
            schema: AUTHORITY_READBACK_SCHEMA,
            trusted_boundary_ready: false,
            expected_generation: expected_generation.map(|value| hex_lower(value)),
            generation_bound: expected_generation.is_some(),
            diagnostic_only: expected_generation.is_none(),
            service_installed: true,
            service_running: running,
            service_binary_path_exact,
            service_executable_path_exact,
            service_arguments_exact,
            service_account_exact,
            service_type_exact,
            service_start_exact,
            service_error_control_exact,
            service_dacl_exact,
            service_sid_restricted,
            required_privileges_exact,
            running_process_id,
            running_process_creation_time: None,
            running_process_identity_exact: false,
            running_image_path_exact: false,
            running_image_file_identity_exact: false,
            running_image_sha256_exact: false,
            running_image_generation_handshake_exact: false,
            controller_launch_receipt_exact: false,
            binary_anchor_chain_exact: false,
            state_anchor_chain_exact: false,
            generation_payloads_exact: false,
            signing_key_exact: false,
            ledger_exact: false,
            trust_manifest_exact: false,
            activation_manifest_exact: false,
            retirement_state_exact: false,
            recovery_state_exact: false,
            protected_readback_complete: false,
            blockers: PERMANENT_BLOCKERS.into_iter().map(str::to_string).collect(),
        };
        readback.push_if_false(
            expected_generation.is_some(),
            "authority_generation_required_for_readback",
        );
        readback.push_if_false(
            service_binary_path_exact,
            "authority_service_binary_path_mismatch",
        );
        readback.push_if_false(
            service_executable_path_exact,
            "authority_service_executable_path_mismatch",
        );
        readback.push_if_false(
            service_arguments_exact,
            "authority_service_arguments_mismatch",
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
#[path = "primitive_evidence_authority_windows/tests.rs"]
mod tests;

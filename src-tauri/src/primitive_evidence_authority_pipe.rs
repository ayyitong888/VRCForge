use sha2::{Digest, Sha256};
use std::{
    fmt,
    path::{Component, Path, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
};

use crate::primitive_evidence_authority_windows::{
    AuthorityLayout, AUTHORITY_PIPE_NAME, AUTHORITY_PIPE_SDDL,
};

#[path = "primitive_evidence_authority_pipe/handle_tokens.rs"]
mod handle_tokens;
pub use handle_tokens::{
    AdmittedExternalModelPartHandles, ExternalModelPartHandleTokens,
    EXTERNAL_MODEL_PART_HANDLE_COUNT, EXTERNAL_MODEL_PART_HANDLE_ROLES,
    FIXED_MODEL_PART_HANDLE_COUNT, FIXED_MODEL_PART_HANDLE_ROLES,
};

const PIPE_BUFFER_BYTES: u32 = 64 * 1024;
const PIPE_DEFAULT_TIMEOUT_MS: u32 = 5_000;
const PIPE_IO_TIMEOUT_MS: u32 = 5_000;
const PIPE_CANCEL_SETTLE_TIMEOUT_MS: u32 = 5_000;
const MAX_CONTROLLER_BYTES: u64 = 256 * 1024 * 1024;
const MAX_REQUEST_ID_BYTES: usize = 128;
const MAX_CLAIMED_CONTROLLER_LAUNCHES: usize = 1_024;
const INSTALLED_CONTROLLER_COMMAND_INTENT_DOMAIN: &[u8] =
    b"vrcforge-authority-installed-controller-command-v1\0";

/// A closed, typed description of the single protocol command an installed
/// controller launch is allowed to issue. Handle values are deliberately not
/// part of the intent and remain confined to the pipe admission boundary.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum InstalledControllerCommandIntent {
    Status,
    SelfTest,
    RunModelPartComposition { request_id: String },
    Cancel { request_id: String },
    GetResult { request_id: String },
}

impl InstalledControllerCommandIntent {
    pub fn status() -> Self {
        Self::Status
    }

    pub fn self_test() -> Self {
        Self::SelfTest
    }

    pub fn run_model_part_composition(
        request_id: impl Into<String>,
    ) -> Result<Self, AuthorityPipeError> {
        Ok(Self::RunModelPartComposition {
            request_id: validated_request_id(request_id.into())?,
        })
    }

    pub fn cancel(request_id: impl Into<String>) -> Result<Self, AuthorityPipeError> {
        Ok(Self::Cancel {
            request_id: validated_request_id(request_id.into())?,
        })
    }

    pub fn get_result(request_id: impl Into<String>) -> Result<Self, AuthorityPipeError> {
        Ok(Self::GetResult {
            request_id: validated_request_id(request_id.into())?,
        })
    }

    fn binding_digest(&self) -> [u8; 32] {
        let (tag, request_id) = match self {
            Self::Status => (b"status".as_slice(), None),
            Self::SelfTest => (b"selfTest".as_slice(), None),
            Self::RunModelPartComposition { request_id } => (
                b"runModelPartComposition".as_slice(),
                Some(request_id.as_bytes()),
            ),
            Self::Cancel { request_id } => (b"cancel".as_slice(), Some(request_id.as_bytes())),
            Self::GetResult { request_id } => {
                (b"getResult".as_slice(), Some(request_id.as_bytes()))
            }
        };
        let request_id = request_id.unwrap_or_default();
        let mut digest = Sha256::new();
        digest.update(INSTALLED_CONTROLLER_COMMAND_INTENT_DOMAIN);
        digest.update((tag.len() as u64).to_be_bytes());
        digest.update(tag);
        digest.update((request_id.len() as u64).to_be_bytes());
        digest.update(request_id);
        digest.finalize().into()
    }

    fn validate(&self) -> Result<(), AuthorityPipeError> {
        match self {
            Self::RunModelPartComposition { request_id }
            | Self::Cancel { request_id }
            | Self::GetResult { request_id } => {
                validated_request_id(request_id.clone()).map(|_| ())
            }
            Self::Status | Self::SelfTest => Ok(()),
        }
    }

    fn requires_model_part_handles(&self) -> bool {
        matches!(self, Self::RunModelPartComposition { .. })
    }
}

fn validated_request_id(request_id: String) -> Result<String, AuthorityPipeError> {
    let mut bytes = request_id.bytes();
    let first = bytes
        .next()
        .ok_or_else(|| AuthorityPipeError::new("authority_controller_request_id_invalid"))?;
    if request_id.len() > MAX_REQUEST_ID_BYTES
        || !first.is_ascii_alphanumeric()
        || !bytes.all(|byte| byte.is_ascii_alphanumeric() || b"-_.:".contains(&byte))
    {
        return Err(AuthorityPipeError::new(
            "authority_controller_request_id_invalid",
        ));
    }
    Ok(request_id)
}

#[derive(Debug, Default, Clone)]
pub struct AuthorityConnectionGate {
    state: Arc<AuthorityConnectionState>,
}

#[derive(Debug, Default)]
struct AuthorityConnectionState {
    active: AtomicBool,
    stopping: AtomicBool,
    failed: AtomicBool,
}

#[derive(Debug)]
pub struct AuthorityConnectionLease {
    state: Arc<AuthorityConnectionState>,
    released: bool,
}

impl AuthorityConnectionGate {
    pub fn try_acquire(&self) -> Result<AuthorityConnectionLease, AuthorityPipeError> {
        if self.state.failed.load(Ordering::Acquire) {
            return Err(AuthorityPipeError::new("authority_pipe_failed"));
        }
        if self.state.stopping.load(Ordering::Acquire) {
            return Err(AuthorityPipeError::new("authority_pipe_stopping"));
        }
        self.state
            .active
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .map_err(|_| AuthorityPipeError::new("authority_pipe_connection_busy"))?;
        if self.state.failed.load(Ordering::Acquire) || self.state.stopping.load(Ordering::Acquire)
        {
            self.state.active.store(false, Ordering::Release);
            return Err(AuthorityPipeError::new(
                if self.state.failed.load(Ordering::Acquire) {
                    "authority_pipe_failed"
                } else {
                    "authority_pipe_stopping"
                },
            ));
        }
        Ok(AuthorityConnectionLease {
            state: Arc::clone(&self.state),
            released: false,
        })
    }

    pub fn request_stop(&self) {
        self.state.stopping.store(true, Ordering::Release);
    }

    pub fn latch_failure(&self) {
        self.state.failed.store(true, Ordering::Release);
        self.state.stopping.store(true, Ordering::Release);
    }

    pub fn is_stop_requested(&self) -> bool {
        self.state.stopping.load(Ordering::Acquire) || self.state.failed.load(Ordering::Acquire)
    }

    pub fn has_failed(&self) -> bool {
        self.state.failed.load(Ordering::Acquire)
    }

    pub fn has_active_connection(&self) -> bool {
        self.state.active.load(Ordering::Acquire)
    }
}

impl AuthorityConnectionLease {
    pub fn is_stop_requested(&self) -> bool {
        self.state.stopping.load(Ordering::Acquire) || self.state.failed.load(Ordering::Acquire)
    }

    pub fn release(mut self) {
        self.release_inner();
    }

    fn release_inner(&mut self) {
        if !self.released {
            self.state.active.store(false, Ordering::Release);
            self.released = true;
        }
    }
}

impl Drop for AuthorityConnectionLease {
    fn drop(&mut self) {
        self.release_inner();
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthorityPipeError {
    code: &'static str,
    win32: Option<u32>,
}

impl AuthorityPipeError {
    fn new(code: &'static str) -> Self {
        Self { code, win32: None }
    }

    #[cfg(windows)]
    fn last_win32(code: &'static str) -> Self {
        Self {
            code,
            win32: Some(unsafe { windows_sys::Win32::Foundation::GetLastError() }),
        }
    }

    #[cfg(windows)]
    fn from_io(code: &'static str, error: &std::io::Error) -> Self {
        Self {
            code,
            win32: error
                .raw_os_error()
                .and_then(|value| u32::try_from(value).ok()),
        }
    }

    pub fn code(&self) -> &'static str {
        self.code
    }

    pub fn win32(&self) -> Option<u32> {
        self.win32
    }
}

impl fmt::Display for AuthorityPipeError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self.win32 {
            Some(win32) => write!(formatter, "{} (win32={win32})", self.code),
            None => formatter.write_str(self.code),
        }
    }
}

impl std::error::Error for AuthorityPipeError {}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct StableFileIdentity {
    pub volume_serial_number: u32,
    pub file_index: u64,
    pub size: u64,
    pub creation_time: u64,
    pub last_write_time: u64,
    pub link_count: u32,
}

impl StableFileIdentity {
    pub fn binding_digest(&self) -> [u8; 32] {
        let mut digest = Sha256::new();
        digest.update(b"vrcforge-authority-file-identity-v1\0");
        digest.update(self.volume_serial_number.to_be_bytes());
        digest.update(self.file_index.to_be_bytes());
        digest.update(self.size.to_be_bytes());
        digest.update(self.creation_time.to_be_bytes());
        digest.update(self.last_write_time.to_be_bytes());
        digest.update(self.link_count.to_be_bytes());
        digest.finalize().into()
    }
}

enum VerifiedControllerLaunchBinding {
    #[cfg(windows)]
    Held(windows::VerifiedControllerLaunchObjects),
    #[cfg(test)]
    TestOnly,
    #[cfg(not(any(windows, test)))]
    Unsupported,
}

pub struct VerifiedControllerLaunchReceipt {
    authority_generation_sha256: [u8; 32],
    controller_path: PathBuf,
    controller_sha256: [u8; 32],
    session_id: u32,
    process_id: u32,
    process_creation_time: u64,
    running_image_file_identity: StableFileIdentity,
    protected_launcher_receipt_sha256: [u8; 32],
    binding: VerifiedControllerLaunchBinding,
}

impl fmt::Debug for VerifiedControllerLaunchReceipt {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("VerifiedControllerLaunchReceipt")
            .field(
                "authority_generation_sha256",
                &hex_lower(&self.authority_generation_sha256),
            )
            .field("controller_path", &self.controller_path)
            .field("controller_sha256", &hex_lower(&self.controller_sha256))
            .field("session_id", &self.session_id)
            .field("process_id", &self.process_id)
            .field("process_creation_time", &self.process_creation_time)
            .field(
                "running_image_file_identity",
                &self.running_image_file_identity,
            )
            .field(
                "protected_launcher_receipt_sha256",
                &hex_lower(&self.protected_launcher_receipt_sha256),
            )
            .finish_non_exhaustive()
    }
}

impl VerifiedControllerLaunchReceipt {
    #[cfg(test)]
    fn for_test(
        authority_generation_sha256: [u8; 32],
        controller_path: PathBuf,
        controller_sha256: [u8; 32],
        session_id: u32,
        process_id: u32,
        process_creation_time: u64,
        running_image_file_identity: StableFileIdentity,
        protected_launcher_receipt_sha256: [u8; 32],
    ) -> Self {
        Self {
            authority_generation_sha256,
            controller_path,
            controller_sha256,
            session_id,
            process_id,
            process_creation_time,
            running_image_file_identity,
            protected_launcher_receipt_sha256,
            binding: VerifiedControllerLaunchBinding::TestOnly,
        }
    }

    #[cfg(windows)]
    fn held_objects(&self) -> Option<&windows::VerifiedControllerLaunchObjects> {
        match &self.binding {
            VerifiedControllerLaunchBinding::Held(value) => Some(value),
            #[cfg(test)]
            VerifiedControllerLaunchBinding::TestOnly => None,
        }
    }
}

#[derive(Debug)]
pub struct AuthorityPeerPolicy {
    expected_controller_path: PathBuf,
    verified_launch: VerifiedControllerLaunchReceipt,
}

impl AuthorityPeerPolicy {
    pub fn for_installed_generation(
        layout: &AuthorityLayout,
        verified_launch: VerifiedControllerLaunchReceipt,
    ) -> Result<Self, AuthorityPipeError> {
        let path = layout
            .controller_executable_for_generation(&verified_launch.authority_generation_sha256)
            .map_err(|_| AuthorityPipeError::new("authority_peer_controller_layout_invalid"))?;
        if !path.is_absolute()
            || path.as_os_str().is_empty()
            || path
                .components()
                .any(|component| matches!(component, Component::CurDir | Component::ParentDir))
        {
            return Err(AuthorityPipeError::new(
                "authority_peer_controller_path_invalid",
            ));
        }
        if verified_launch.controller_path != path {
            return Err(AuthorityPipeError::new(
                "authority_peer_controller_launch_path_mismatch",
            ));
        }
        if verified_launch
            .controller_sha256
            .iter()
            .all(|byte| *byte == 0)
        {
            return Err(AuthorityPipeError::new(
                "authority_peer_controller_digest_invalid",
            ));
        }
        if verified_launch.process_id == 0 || verified_launch.process_creation_time == 0 {
            return Err(AuthorityPipeError::new(
                "authority_peer_process_receipt_invalid",
            ));
        }
        if verified_launch
            .running_image_file_identity
            .volume_serial_number
            == 0
            || verified_launch.running_image_file_identity.file_index == 0
            || verified_launch.running_image_file_identity.creation_time == 0
            || verified_launch.running_image_file_identity.link_count == 0
            || verified_launch.running_image_file_identity.size == 0
            || verified_launch.running_image_file_identity.size > MAX_CONTROLLER_BYTES
        {
            return Err(AuthorityPipeError::new(
                "authority_peer_running_image_identity_invalid",
            ));
        }
        if verified_launch
            .protected_launcher_receipt_sha256
            .iter()
            .all(|byte| *byte == 0)
        {
            return Err(AuthorityPipeError::new(
                "authority_peer_launcher_receipt_invalid",
            ));
        }
        Ok(Self {
            expected_controller_path: path,
            verified_launch,
        })
    }

    pub fn expected_controller_path(&self) -> &Path {
        &self.expected_controller_path
    }

    pub fn expected_controller_sha256(&self) -> &[u8; 32] {
        &self.verified_launch.controller_sha256
    }

    pub fn expected_session_id(&self) -> u32 {
        self.verified_launch.session_id
    }

    pub fn expected_process_id(&self) -> u32 {
        self.verified_launch.process_id
    }

    pub fn expected_process_creation_time(&self) -> u64 {
        self.verified_launch.process_creation_time
    }

    pub fn expected_running_image_file_identity(&self) -> StableFileIdentity {
        self.verified_launch.running_image_file_identity
    }

    pub fn expected_launcher_receipt_sha256(&self) -> &[u8; 32] {
        &self.verified_launch.protected_launcher_receipt_sha256
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

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct AuthorityPeerFacts<'a> {
    pub process_id: u32,
    pub process_creation_time: u64,
    pub controller_path: &'a Path,
    pub controller_sha256: [u8; 32],
    pub running_image_file_identity: StableFileIdentity,
    pub protected_launcher_receipt_sha256: [u8; 32],
    pub running_process_handle_bound: bool,
    pub running_image_object_bound: bool,
    pub pipe_session_id: u32,
    pub token_session_id: u32,
    pub elevated: bool,
    pub high_integrity: bool,
    pub administrators_member: bool,
}

pub fn evaluate_peer_policy(
    policy: &AuthorityPeerPolicy,
    facts: &AuthorityPeerFacts<'_>,
) -> Result<(), AuthorityPipeError> {
    evaluate_peer_pre_hash_policy(policy, facts)?;
    if facts.controller_sha256 != *policy.expected_controller_sha256() {
        return Err(AuthorityPipeError::new(
            "authority_peer_controller_digest_mismatch",
        ));
    }
    if facts.running_image_file_identity != policy.expected_running_image_file_identity() {
        return Err(AuthorityPipeError::new(
            "authority_peer_running_image_identity_mismatch",
        ));
    }
    if facts.protected_launcher_receipt_sha256 != *policy.expected_launcher_receipt_sha256() {
        return Err(AuthorityPipeError::new(
            "authority_peer_launcher_receipt_mismatch",
        ));
    }
    Ok(())
}

fn evaluate_peer_pre_hash_policy(
    policy: &AuthorityPeerPolicy,
    facts: &AuthorityPeerFacts<'_>,
) -> Result<(), AuthorityPipeError> {
    if !facts.elevated {
        return Err(AuthorityPipeError::new("authority_peer_not_elevated"));
    }
    if !facts.high_integrity {
        return Err(AuthorityPipeError::new("authority_peer_integrity_too_low"));
    }
    if !facts.administrators_member {
        return Err(AuthorityPipeError::new("authority_peer_not_administrator"));
    }
    if facts.pipe_session_id != facts.token_session_id
        || facts.pipe_session_id != policy.expected_session_id()
    {
        return Err(AuthorityPipeError::new("authority_peer_session_mismatch"));
    }
    if facts.process_id != policy.expected_process_id()
        || facts.process_creation_time != policy.expected_process_creation_time()
    {
        return Err(AuthorityPipeError::new(
            "authority_peer_process_receipt_mismatch",
        ));
    }
    if !facts.running_process_handle_bound {
        return Err(AuthorityPipeError::new(
            "authority_peer_process_handle_unbound",
        ));
    }
    if !facts.running_image_object_bound {
        return Err(AuthorityPipeError::new(
            "authority_peer_running_image_object_unbound",
        ));
    }
    if facts.controller_path != policy.expected_controller_path {
        return Err(AuthorityPipeError::new(
            "authority_peer_controller_path_mismatch",
        ));
    }
    Ok(())
}

#[cfg(windows)]
mod windows {
    use super::*;
    use crate::primitive_evidence_authority_install::bootstrap::{
        AuthenticatedControllerSourceReadback, AuthenticatedInstallHelperSourceReadback,
    };
    use std::{
        collections::BTreeSet,
        ffi::OsString,
        fs::{File, OpenOptions},
        io::{self, Read, Seek, SeekFrom, Write},
        mem::{size_of, zeroed},
        ops::Deref,
        os::windows::{
            ffi::{OsStrExt, OsStringExt},
            fs::{FileExt, OpenOptionsExt},
            io::{AsHandle, AsRawHandle, BorrowedHandle, FromRawHandle, OwnedHandle, RawHandle},
        },
        ptr,
        sync::{atomic::AtomicU8, Mutex, OnceLock},
    };
    use windows_sys::Wdk::Foundation::{NtQueryObject, ObjectBasicInformation};
    use windows_sys::Win32::{
        Foundation::{
            DuplicateHandle, GetHandleInformation, GetLastError, LocalFree, DUPLICATE_SAME_ACCESS,
            ERROR_BROKEN_PIPE, ERROR_INSUFFICIENT_BUFFER, ERROR_IO_PENDING, ERROR_MORE_DATA,
            ERROR_NOT_FOUND, ERROR_OPERATION_ABORTED, ERROR_PIPE_CONNECTED,
            ERROR_PIPE_NOT_CONNECTED, ERROR_SHARING_VIOLATION, FILETIME, GENERIC_READ,
            GENERIC_WRITE, HANDLE_FLAG_INHERIT, INVALID_HANDLE_VALUE, WAIT_FAILED, WAIT_OBJECT_0,
            WAIT_TIMEOUT,
        },
        Security::{
            Authorization::{
                ConvertStringSecurityDescriptorToSecurityDescriptorW, GetSecurityInfo,
                SDDL_REVISION_1, SE_FILE_OBJECT,
            },
            CreateWellKnownSid, EqualSid, GetLengthSid, GetSecurityDescriptorLength,
            GetSidSubAuthority, GetSidSubAuthorityCount, GetTokenInformation, IsValidSid,
            TokenElevation, TokenGroups, TokenIntegrityLevel, TokenRestrictedSids, TokenSessionId,
            WinBuiltinAdministratorsSid, DACL_SECURITY_INFORMATION, GROUP_SECURITY_INFORMATION,
            OWNER_SECURITY_INFORMATION, SECURITY_ATTRIBUTES, SECURITY_MAX_SID_SIZE,
            SID_AND_ATTRIBUTES, TOKEN_ELEVATION, TOKEN_GROUPS, TOKEN_MANDATORY_LABEL, TOKEN_QUERY,
        },
        Storage::FileSystem::{
            CreateFileW, GetFileInformationByHandle, GetFileType, GetFinalPathNameByHandleW,
            ReOpenFile, ReadFile, WriteFile, BY_HANDLE_FILE_INFORMATION, DELETE, FILE_APPEND_DATA,
            FILE_ATTRIBUTE_DIRECTORY, FILE_ATTRIBUTE_REPARSE_POINT, FILE_DELETE_CHILD,
            FILE_FLAG_FIRST_PIPE_INSTANCE, FILE_FLAG_OPEN_REPARSE_POINT, FILE_FLAG_OVERLAPPED,
            FILE_FLAG_SEQUENTIAL_SCAN, FILE_READ_ATTRIBUTES, FILE_READ_DATA, FILE_READ_EA,
            FILE_SHARE_DELETE, FILE_SHARE_READ, FILE_SHARE_WRITE, FILE_TYPE_DISK,
            FILE_WRITE_ATTRIBUTES, FILE_WRITE_DATA, FILE_WRITE_EA, OPEN_EXISTING,
            PIPE_ACCESS_DUPLEX, READ_CONTROL, SYNCHRONIZE, WRITE_DAC, WRITE_OWNER,
        },
        System::{
            Pipes::{
                ConnectNamedPipe, CreateNamedPipeW, DisconnectNamedPipe,
                GetNamedPipeClientProcessId, GetNamedPipeClientSessionId, PIPE_READMODE_MESSAGE,
                PIPE_REJECT_REMOTE_CLIENTS, PIPE_TYPE_MESSAGE, PIPE_WAIT,
            },
            SystemServices::{
                SECURITY_MANDATORY_HIGH_RID, SE_GROUP_ENABLED, SE_GROUP_USE_FOR_DENY_ONLY,
            },
            Threading::{
                CreateEventW, GetCurrentProcess, GetProcessId, GetProcessTimes, OpenProcess,
                OpenProcessToken, QueryFullProcessImageNameW, WaitForSingleObject,
                PROCESS_DUP_HANDLE, PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_SYNCHRONIZE,
            },
            IO::{CancelIoEx, GetOverlappedResult, OVERLAPPED},
        },
    };

    pub(super) const TEST_PIPE_SDDL: &str = "D:P(A;;GA;;;WD)";

    const INSTALLED_CONTROLLER_LAUNCH_RECEIPT_DOMAIN: &[u8] =
        b"vrcforge-authority-installed-controller-launch-v1\0";
    const INSTALLED_CONTROLLER_LAUNCH_IDENTITY_DOMAIN: &[u8] =
        b"vrcforge-authority-installed-controller-launch-identity-v1\0";
    const INSTALLED_RUNTIME_BROKER_IDENTITY_DOMAIN: &[u8] =
        b"vrcforge-authority-installed-runtime-broker-identity-v1\0";
    const CONTROLLER_LAUNCH_STATE_AUTHENTICATED: u8 = 0;
    const CONTROLLER_LAUNCH_STATE_CONSUMING: u8 = 1;
    const CONTROLLER_LAUNCH_STATE_CONSUMED: u8 = 2;
    const CONTROLLER_LAUNCH_STATE_BURNED: u8 = 3;
    const RUNTIME_BROKER_STATE_AUTHENTICATED: u8 = 0;
    const RUNTIME_BROKER_STATE_BURNED: u8 = 1;

    static CLAIMED_CONTROLLER_LAUNCHES: OnceLock<Mutex<ControllerLaunchRegistry>> = OnceLock::new();
    static CLAIMED_RUNTIME_BROKERS: OnceLock<Mutex<RuntimeBrokerRegistry>> = OnceLock::new();

    #[derive(Debug, Default)]
    struct ControllerLaunchRegistry {
        identities: BTreeSet<[u8; 32]>,
    }

    impl ControllerLaunchRegistry {
        fn claim(&mut self, launch_identity_sha256: [u8; 32]) -> Result<(), AuthorityPipeError> {
            if self.identities.contains(&launch_identity_sha256) {
                return Err(AuthorityPipeError::new(
                    "authority_controller_launch_replayed",
                ));
            }
            if self.identities.len() >= MAX_CLAIMED_CONTROLLER_LAUNCHES {
                return Err(AuthorityPipeError::new(
                    "authority_controller_launch_registry_exhausted",
                ));
            }
            if !self.identities.insert(launch_identity_sha256) {
                return Err(AuthorityPipeError::new(
                    "authority_controller_launch_replayed",
                ));
            }
            Ok(())
        }
    }

    #[derive(Debug, Default)]
    struct RuntimeBrokerRegistry {
        identities: BTreeSet<[u8; 32]>,
    }

    impl RuntimeBrokerRegistry {
        fn claim(&mut self, broker_identity_sha256: [u8; 32]) -> Result<(), AuthorityPipeError> {
            if self.identities.contains(&broker_identity_sha256) {
                return Err(AuthorityPipeError::new(
                    "authority_runtime_broker_admission_replayed",
                ));
            }
            if self.identities.len() >= MAX_CLAIMED_CONTROLLER_LAUNCHES {
                return Err(AuthorityPipeError::new(
                    "authority_runtime_broker_registry_exhausted",
                ));
            }
            if !self.identities.insert(broker_identity_sha256) {
                return Err(AuthorityPipeError::new(
                    "authority_runtime_broker_admission_replayed",
                ));
            }
            Ok(())
        }
    }

    #[derive(Debug)]
    struct InstalledControllerSourcePolicy {
        generation: [u8; 32],
        service_process_id: u32,
        service_process_started_at: u64,
        controller_path: PathBuf,
        controller_sha256: [u8; 32],
        controller_byte_length: u64,
        volume_serial: u32,
        file_id: u64,
        link_count: u32,
        installed_layout_sha256: [u8; 32],
        final_commit_receipt_sha256: [u8; 32],
        source_binding_sha256: [u8; 32],
        source_lease: InstalledControllerSourceLease,
    }

    #[derive(Debug)]
    enum InstalledControllerSourceLease {
        Authenticated(AuthenticatedControllerSourceReadback),
        #[cfg(test)]
        Synthetic,
    }

    impl InstalledControllerSourcePolicy {
        fn from_authenticated_source(source: AuthenticatedControllerSourceReadback) -> Self {
            Self {
                generation: *source.generation(),
                service_process_id: source.service_process_id(),
                service_process_started_at: source.service_process_started_at(),
                controller_path: source.controller_path().to_path_buf(),
                controller_sha256: *source.controller_sha256(),
                controller_byte_length: source.controller_byte_length(),
                volume_serial: source.volume_serial(),
                file_id: source.file_id(),
                link_count: source.link_count(),
                installed_layout_sha256: *source.installed_layout_sha256(),
                final_commit_receipt_sha256: *source.final_commit_receipt_sha256(),
                source_binding_sha256: *source.source_binding_sha256(),
                source_lease: InstalledControllerSourceLease::Authenticated(source),
            }
        }

        fn validate(&self) -> Result<(), AuthorityPipeError> {
            match &self.source_lease {
                InstalledControllerSourceLease::Authenticated(source) => source
                    .verify_still_stable()
                    .map_err(|error| AuthorityPipeError::new(error.code()))?,
                #[cfg(test)]
                InstalledControllerSourceLease::Synthetic => {}
            }
            if self.generation.iter().all(|byte| *byte == 0)
                || self.controller_sha256.iter().all(|byte| *byte == 0)
                || self.installed_layout_sha256.iter().all(|byte| *byte == 0)
                || self
                    .final_commit_receipt_sha256
                    .iter()
                    .all(|byte| *byte == 0)
                || self.source_binding_sha256.iter().all(|byte| *byte == 0)
            {
                return Err(AuthorityPipeError::new(
                    "authority_installed_controller_source_digest_invalid",
                ));
            }
            if self.controller_path.as_os_str().is_empty()
                || !self.controller_path.is_absolute()
                || self
                    .controller_path
                    .components()
                    .any(|component| matches!(component, Component::CurDir | Component::ParentDir))
            {
                return Err(AuthorityPipeError::new(
                    "authority_installed_controller_source_path_invalid",
                ));
            }
            if self.controller_byte_length == 0
                || self.controller_byte_length > MAX_CONTROLLER_BYTES
                || self.service_process_id == 0
                || self.service_process_started_at == 0
                || self.volume_serial == 0
                || self.file_id == 0
                || self.link_count != 1
            {
                return Err(AuthorityPipeError::new(
                    "authority_installed_controller_source_identity_invalid",
                ));
            }
            Ok(())
        }
    }

    /// A production controller policy can only be projected from the opaque
    /// authenticated FinalCommit readback. It intentionally has no Clone
    /// implementation. The first parsed request binds the command later.
    pub struct InstalledControllerPolicy {
        source: InstalledControllerSourcePolicy,
    }

    impl fmt::Debug for InstalledControllerPolicy {
        fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
            formatter
                .debug_struct("InstalledControllerPolicy")
                .field("generation", &hex_lower(&self.source.generation))
                .finish_non_exhaustive()
        }
    }

    impl InstalledControllerPolicy {
        pub(crate) fn from_authenticated_source(
            source: AuthenticatedControllerSourceReadback,
        ) -> Result<Self, AuthorityPipeError> {
            let source = InstalledControllerSourcePolicy::from_authenticated_source(source);
            source.validate()?;
            Ok(Self { source })
        }

        #[cfg(test)]
        pub(super) fn for_test(
            generation: [u8; 32],
            service_process_id: u32,
            service_process_started_at: u64,
            controller_path: PathBuf,
            controller_sha256: [u8; 32],
            running_image_file_identity: StableFileIdentity,
            installed_layout_sha256: [u8; 32],
            final_commit_receipt_sha256: [u8; 32],
            source_binding_sha256: [u8; 32],
        ) -> Result<Self, AuthorityPipeError> {
            let source = InstalledControllerSourcePolicy {
                generation,
                service_process_id,
                service_process_started_at,
                controller_path,
                controller_sha256,
                controller_byte_length: running_image_file_identity.size,
                volume_serial: running_image_file_identity.volume_serial_number,
                file_id: running_image_file_identity.file_index,
                link_count: running_image_file_identity.link_count,
                installed_layout_sha256,
                final_commit_receipt_sha256,
                source_binding_sha256,
                source_lease: InstalledControllerSourceLease::Synthetic,
            };
            source.validate()?;
            Ok(Self { source })
        }
    }

    #[derive(Debug)]
    struct InstalledRuntimeBrokerSourcePolicy {
        generation: [u8; 32],
        service_process_id: u32,
        service_process_started_at: u64,
        install_helper_path: PathBuf,
        install_helper_sha256: [u8; 32],
        install_helper_byte_length: u64,
        volume_serial: u32,
        file_id: u64,
        link_count: u32,
        installed_layout_sha256: [u8; 32],
        final_commit_receipt_sha256: [u8; 32],
        source_binding_sha256: [u8; 32],
        source_lease: InstalledRuntimeBrokerSourceLease,
    }

    #[derive(Debug)]
    enum InstalledRuntimeBrokerSourceLease {
        Authenticated(AuthenticatedInstallHelperSourceReadback),
        #[cfg(test)]
        Synthetic,
    }

    impl InstalledRuntimeBrokerSourcePolicy {
        fn from_authenticated_source(source: AuthenticatedInstallHelperSourceReadback) -> Self {
            Self {
                generation: *source.generation(),
                service_process_id: source.service_process_id(),
                service_process_started_at: source.service_process_started_at(),
                install_helper_path: source.install_helper_path().to_path_buf(),
                install_helper_sha256: *source.install_helper_sha256(),
                install_helper_byte_length: source.install_helper_byte_length(),
                volume_serial: source.volume_serial(),
                file_id: source.file_id(),
                link_count: source.link_count(),
                installed_layout_sha256: *source.installed_layout_sha256(),
                final_commit_receipt_sha256: *source.final_commit_receipt_sha256(),
                source_binding_sha256: *source.source_binding_sha256(),
                source_lease: InstalledRuntimeBrokerSourceLease::Authenticated(source),
            }
        }

        fn validate(&self) -> Result<(), AuthorityPipeError> {
            match &self.source_lease {
                InstalledRuntimeBrokerSourceLease::Authenticated(source) => source
                    .verify_still_stable()
                    .map_err(|error| AuthorityPipeError::new(error.code()))?,
                #[cfg(test)]
                InstalledRuntimeBrokerSourceLease::Synthetic => {}
            }
            if self.generation.iter().all(|byte| *byte == 0)
                || self.install_helper_sha256.iter().all(|byte| *byte == 0)
                || self.installed_layout_sha256.iter().all(|byte| *byte == 0)
                || self
                    .final_commit_receipt_sha256
                    .iter()
                    .all(|byte| *byte == 0)
                || self.source_binding_sha256.iter().all(|byte| *byte == 0)
            {
                return Err(AuthorityPipeError::new(
                    "authority_installed_runtime_broker_source_digest_invalid",
                ));
            }
            if self.install_helper_path.as_os_str().is_empty()
                || !self.install_helper_path.is_absolute()
                || self
                    .install_helper_path
                    .components()
                    .any(|component| matches!(component, Component::CurDir | Component::ParentDir))
            {
                return Err(AuthorityPipeError::new(
                    "authority_installed_runtime_broker_source_path_invalid",
                ));
            }
            if self.install_helper_byte_length == 0
                || self.install_helper_byte_length > MAX_CONTROLLER_BYTES
                || self.service_process_id == 0
                || self.service_process_started_at == 0
                || self.volume_serial == 0
                || self.file_id == 0
                || self.link_count != 1
            {
                return Err(AuthorityPipeError::new(
                    "authority_installed_runtime_broker_source_identity_invalid",
                ));
            }
            Ok(())
        }
    }

    /// One exact, elevated runtime broker source projected from the held
    /// FinalCommit helper object. The policy is consumed by accept and is not
    /// clonable or constructible from loose path/digest values in production.
    pub struct InstalledRuntimeBrokerPolicy {
        source: InstalledRuntimeBrokerSourcePolicy,
    }

    impl fmt::Debug for InstalledRuntimeBrokerPolicy {
        fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
            formatter
                .debug_struct("InstalledRuntimeBrokerPolicy")
                .field("generation", &hex_lower(&self.source.generation))
                .finish_non_exhaustive()
        }
    }

    impl InstalledRuntimeBrokerPolicy {
        pub(crate) fn from_authenticated_source(
            source: AuthenticatedInstallHelperSourceReadback,
        ) -> Result<Self, AuthorityPipeError> {
            let source = InstalledRuntimeBrokerSourcePolicy::from_authenticated_source(source);
            source.validate()?;
            Ok(Self { source })
        }

        #[cfg(test)]
        pub(super) fn for_test(
            generation: [u8; 32],
            service_process_id: u32,
            service_process_started_at: u64,
            install_helper_path: PathBuf,
            install_helper_sha256: [u8; 32],
            running_image_file_identity: StableFileIdentity,
            installed_layout_sha256: [u8; 32],
            final_commit_receipt_sha256: [u8; 32],
            source_binding_sha256: [u8; 32],
        ) -> Result<Self, AuthorityPipeError> {
            let source = InstalledRuntimeBrokerSourcePolicy {
                generation,
                service_process_id,
                service_process_started_at,
                install_helper_path,
                install_helper_sha256,
                install_helper_byte_length: running_image_file_identity.size,
                volume_serial: running_image_file_identity.volume_serial_number,
                file_id: running_image_file_identity.file_index,
                link_count: running_image_file_identity.link_count,
                installed_layout_sha256,
                final_commit_receipt_sha256,
                source_binding_sha256,
                source_lease: InstalledRuntimeBrokerSourceLease::Synthetic,
            };
            source.validate()?;
            Ok(Self { source })
        }
    }

    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    pub enum ControllerLaunchState {
        Authenticated,
        Consuming,
        Consumed,
        Burned,
    }

    #[derive(Debug, Clone, Copy)]
    struct InstalledControllerLaunchSeed {
        source_binding_sha256: [u8; 32],
        process_id: u32,
        process_creation_time: u64,
        session_id: u32,
        running_image_file_identity: StableFileIdentity,
        launch_identity_sha256: [u8; 32],
    }

    #[derive(Debug)]
    struct InstalledControllerLaunchState {
        seed: InstalledControllerLaunchSeed,
        receipt_sha256: OnceLock<[u8; 32]>,
        state: Arc<AtomicU8>,
    }

    struct ConsumingControllerCommand<'a> {
        state: Arc<AtomicU8>,
        scenario_handles: &'a PendingScenarioHandleBundle,
        receipt_sha256: &'a OnceLock<[u8; 32]>,
        pending_receipt_sha256: Option<[u8; 32]>,
        completed: bool,
    }

    impl InstalledControllerLaunchState {
        fn new(seed: InstalledControllerLaunchSeed) -> Self {
            Self {
                seed,
                receipt_sha256: OnceLock::new(),
                state: Arc::new(AtomicU8::new(CONTROLLER_LAUNCH_STATE_AUTHENTICATED)),
            }
        }

        fn state(&self) -> ControllerLaunchState {
            controller_launch_state(self.state.load(Ordering::Acquire))
        }

        fn receipt_sha256(&self) -> Option<[u8; 32]> {
            if self.state() != ControllerLaunchState::Consumed {
                return None;
            }
            self.receipt_sha256.get().copied()
        }

        fn begin_command<'a>(
            &'a self,
            observed: &InstalledControllerCommandIntent,
            scenario_handles: &'a PendingScenarioHandleBundle,
        ) -> Result<ConsumingControllerCommand<'a>, AuthorityPipeError> {
            self.state
                .compare_exchange(
                    CONTROLLER_LAUNCH_STATE_AUTHENTICATED,
                    CONTROLLER_LAUNCH_STATE_CONSUMING,
                    Ordering::AcqRel,
                    Ordering::Acquire,
                )
                .map_err(|_| {
                    AuthorityPipeError::new("authority_controller_launch_already_consumed")
                })?;
            let mut consuming = ConsumingControllerCommand {
                state: Arc::clone(&self.state),
                scenario_handles,
                receipt_sha256: &self.receipt_sha256,
                pending_receipt_sha256: None,
                completed: false,
            };
            observed.validate()?;
            consuming.pending_receipt_sha256 = Some(self.seed.receipt_sha256(observed));
            Ok(consuming)
        }
    }

    impl InstalledControllerLaunchSeed {
        fn receipt_sha256(&self, command_intent: &InstalledControllerCommandIntent) -> [u8; 32] {
            let mut digest = Sha256::new();
            digest.update(INSTALLED_CONTROLLER_LAUNCH_RECEIPT_DOMAIN);
            digest.update(self.source_binding_sha256);
            digest.update(self.process_id.to_be_bytes());
            digest.update(self.process_creation_time.to_be_bytes());
            digest.update(self.session_id.to_be_bytes());
            digest.update(self.running_image_file_identity.binding_digest());
            digest.update(command_intent.binding_digest());
            digest.finalize().into()
        }
    }

    impl Drop for InstalledControllerLaunchState {
        fn drop(&mut self) {
            let _ = self.state.compare_exchange(
                CONTROLLER_LAUNCH_STATE_AUTHENTICATED,
                CONTROLLER_LAUNCH_STATE_BURNED,
                Ordering::AcqRel,
                Ordering::Acquire,
            );
            let _ = self.state.compare_exchange(
                CONTROLLER_LAUNCH_STATE_CONSUMING,
                CONTROLLER_LAUNCH_STATE_BURNED,
                Ordering::AcqRel,
                Ordering::Acquire,
            );
        }
    }

    impl ConsumingControllerCommand<'_> {
        fn finish(mut self) -> Result<(), AuthorityPipeError> {
            let receipt_sha256 = self.pending_receipt_sha256.ok_or_else(|| {
                AuthorityPipeError::new("authority_controller_launch_receipt_unavailable")
            })?;
            self.receipt_sha256.set(receipt_sha256).map_err(|_| {
                AuthorityPipeError::new("authority_controller_launch_receipt_reused")
            })?;
            self.state
                .compare_exchange(
                    CONTROLLER_LAUNCH_STATE_CONSUMING,
                    CONTROLLER_LAUNCH_STATE_CONSUMED,
                    Ordering::AcqRel,
                    Ordering::Acquire,
                )
                .map_err(|_| {
                    AuthorityPipeError::new("authority_controller_launch_state_invalid")
                })?;
            self.completed = true;
            Ok(())
        }
    }

    impl Drop for ConsumingControllerCommand<'_> {
        fn drop(&mut self) {
            if !self.completed {
                self.state
                    .store(CONTROLLER_LAUNCH_STATE_BURNED, Ordering::Release);
                let _ = self.scenario_handles.burn();
            }
        }
    }

    fn controller_launch_state(value: u8) -> ControllerLaunchState {
        match value {
            CONTROLLER_LAUNCH_STATE_AUTHENTICATED => ControllerLaunchState::Authenticated,
            CONTROLLER_LAUNCH_STATE_CONSUMING => ControllerLaunchState::Consuming,
            CONTROLLER_LAUNCH_STATE_CONSUMED => ControllerLaunchState::Consumed,
            _ => ControllerLaunchState::Burned,
        }
    }

    fn validate_installed_controller_facts(
        source: &InstalledControllerSourcePolicy,
        facts: &AuthorityPeerFacts<'_>,
    ) -> Result<(), AuthorityPipeError> {
        source.validate()?;
        if facts.process_id == 0 || facts.process_creation_time == 0 {
            return Err(AuthorityPipeError::new(
                "authority_peer_process_receipt_invalid",
            ));
        }
        if facts.process_id == source.service_process_id {
            return Err(AuthorityPipeError::new(
                "authority_controller_process_is_service",
            ));
        }
        if facts.process_creation_time <= source.service_process_started_at {
            return Err(AuthorityPipeError::new(
                "authority_controller_predates_service",
            ));
        }
        if !facts.elevated {
            return Err(AuthorityPipeError::new("authority_peer_not_elevated"));
        }
        if !facts.high_integrity {
            return Err(AuthorityPipeError::new("authority_peer_integrity_too_low"));
        }
        if !facts.administrators_member {
            return Err(AuthorityPipeError::new("authority_peer_not_administrator"));
        }
        if facts.pipe_session_id != facts.token_session_id {
            return Err(AuthorityPipeError::new("authority_peer_session_mismatch"));
        }
        if !facts.running_process_handle_bound {
            return Err(AuthorityPipeError::new(
                "authority_peer_process_handle_unbound",
            ));
        }
        if !facts.running_image_object_bound {
            return Err(AuthorityPipeError::new(
                "authority_peer_running_image_object_unbound",
            ));
        }
        if facts.controller_path != source.controller_path {
            return Err(AuthorityPipeError::new(
                "authority_peer_controller_path_mismatch",
            ));
        }
        if facts.controller_sha256 != source.controller_sha256 {
            return Err(AuthorityPipeError::new(
                "authority_peer_controller_digest_mismatch",
            ));
        }
        let identity = facts.running_image_file_identity;
        if identity.volume_serial_number != source.volume_serial
            || identity.file_index != source.file_id
            || identity.size != source.controller_byte_length
            || identity.link_count != source.link_count
            || identity.creation_time == 0
        {
            return Err(AuthorityPipeError::new(
                "authority_peer_running_image_identity_mismatch",
            ));
        }
        Ok(())
    }

    fn derive_installed_controller_launch_seed(
        source: &InstalledControllerSourcePolicy,
        facts: &AuthorityPeerFacts<'_>,
    ) -> Result<InstalledControllerLaunchSeed, AuthorityPipeError> {
        validate_installed_controller_facts(source, facts)?;
        let mut digest = Sha256::new();
        digest.update(INSTALLED_CONTROLLER_LAUNCH_IDENTITY_DOMAIN);
        digest.update(source.source_binding_sha256);
        digest.update(facts.process_id.to_be_bytes());
        digest.update(facts.process_creation_time.to_be_bytes());
        digest.update(facts.pipe_session_id.to_be_bytes());
        digest.update(facts.running_image_file_identity.binding_digest());
        let launch_identity_sha256: [u8; 32] = digest.finalize().into();
        Ok(InstalledControllerLaunchSeed {
            source_binding_sha256: source.source_binding_sha256,
            process_id: facts.process_id,
            process_creation_time: facts.process_creation_time,
            session_id: facts.pipe_session_id,
            running_image_file_identity: facts.running_image_file_identity,
            launch_identity_sha256,
        })
    }

    fn claim_installed_controller_launch(
        seed: &InstalledControllerLaunchSeed,
    ) -> Result<(), AuthorityPipeError> {
        let registry = CLAIMED_CONTROLLER_LAUNCHES
            .get_or_init(|| Mutex::new(ControllerLaunchRegistry::default()));
        let mut claimed = registry.lock().map_err(|_| {
            AuthorityPipeError::new("authority_controller_launch_registry_unavailable")
        })?;
        claimed.claim(seed.launch_identity_sha256)
    }

    fn validate_installed_runtime_broker_facts(
        source: &InstalledRuntimeBrokerSourcePolicy,
        facts: &AuthorityPeerFacts<'_>,
    ) -> Result<(), AuthorityPipeError> {
        source.validate()?;
        if facts.process_id == 0 || facts.process_creation_time == 0 {
            return Err(AuthorityPipeError::new(
                "authority_peer_process_receipt_invalid",
            ));
        }
        if facts.process_id == source.service_process_id {
            return Err(AuthorityPipeError::new(
                "authority_runtime_broker_process_is_service",
            ));
        }
        if facts.process_creation_time >= source.service_process_started_at {
            return Err(AuthorityPipeError::new(
                "authority_runtime_broker_did_not_precede_service",
            ));
        }
        if !facts.elevated {
            return Err(AuthorityPipeError::new("authority_peer_not_elevated"));
        }
        if !facts.high_integrity {
            return Err(AuthorityPipeError::new("authority_peer_integrity_too_low"));
        }
        if !facts.administrators_member {
            return Err(AuthorityPipeError::new("authority_peer_not_administrator"));
        }
        if facts.pipe_session_id != facts.token_session_id {
            return Err(AuthorityPipeError::new("authority_peer_session_mismatch"));
        }
        if !facts.running_process_handle_bound {
            return Err(AuthorityPipeError::new(
                "authority_peer_process_handle_unbound",
            ));
        }
        if !facts.running_image_object_bound {
            return Err(AuthorityPipeError::new(
                "authority_peer_running_image_object_unbound",
            ));
        }
        if facts.controller_path != source.install_helper_path {
            return Err(AuthorityPipeError::new(
                "authority_runtime_broker_path_mismatch",
            ));
        }
        if facts.controller_sha256 != source.install_helper_sha256 {
            return Err(AuthorityPipeError::new(
                "authority_runtime_broker_digest_mismatch",
            ));
        }
        let identity = facts.running_image_file_identity;
        if identity.volume_serial_number != source.volume_serial
            || identity.file_index != source.file_id
            || identity.size != source.install_helper_byte_length
            || identity.link_count != source.link_count
            || identity.creation_time == 0
        {
            return Err(AuthorityPipeError::new(
                "authority_runtime_broker_image_identity_mismatch",
            ));
        }
        Ok(())
    }

    fn derive_installed_runtime_broker_identity(
        source: &InstalledRuntimeBrokerSourcePolicy,
        facts: &AuthorityPeerFacts<'_>,
    ) -> Result<[u8; 32], AuthorityPipeError> {
        validate_installed_runtime_broker_facts(source, facts)?;
        let mut digest = Sha256::new();
        digest.update(INSTALLED_RUNTIME_BROKER_IDENTITY_DOMAIN);
        digest.update(source.source_binding_sha256);
        digest.update(facts.process_id.to_be_bytes());
        digest.update(facts.process_creation_time.to_be_bytes());
        digest.update(facts.pipe_session_id.to_be_bytes());
        digest.update(facts.running_image_file_identity.binding_digest());
        Ok(digest.finalize().into())
    }

    fn claim_installed_runtime_broker(
        broker_identity_sha256: [u8; 32],
    ) -> Result<(), AuthorityPipeError> {
        let registry =
            CLAIMED_RUNTIME_BROKERS.get_or_init(|| Mutex::new(RuntimeBrokerRegistry::default()));
        let mut claimed = registry.lock().map_err(|_| {
            AuthorityPipeError::new("authority_runtime_broker_registry_unavailable")
        })?;
        claimed.claim(broker_identity_sha256)
    }

    const SCENARIO_HANDLE_STATE_PENDING: u8 = 0;
    const SCENARIO_HANDLE_STATE_CONSUMING: u8 = 1;
    const SCENARIO_HANDLE_STATE_ACTIVE: u8 = 2;
    const SCENARIO_HANDLE_STATE_BURNED: u8 = 3;
    const WORKER_HANDLE_CLONE_FAILED: &str = "authority_model_part_worker_handle_clone_failed";
    const WORKER_HANDLE_BINDING_MISMATCH: &str =
        "authority_model_part_worker_handle_binding_mismatch";
    const WORKER_HANDLE_SNAPSHOT_INVALID: &str =
        "authority_model_part_worker_handle_snapshot_invalid";
    const WORKER_HANDLE_SNAPSHOT_MISMATCH: &str =
        "authority_model_part_worker_handle_snapshot_mismatch";
    const WORKER_HANDLE_ACCESS_INVALID: &str = "authority_model_part_worker_handle_access_invalid";
    const WORKER_HANDLE_SHARING_INVALID: &str =
        "authority_model_part_worker_handle_sharing_invalid";
    const SCENARIO_GUARD_OPEN_FAILED: &str = "authority_model_part_service_guard_open_failed";
    const SCENARIO_GUARD_BINDING_MISMATCH: &str =
        "authority_model_part_service_guard_binding_mismatch";
    const SCENARIO_GUARD_INVALID: &str = "authority_model_part_service_guard_invalid";
    const WORKER_HANDLE_SNAPSHOT_DOMAIN: &[u8] = b"vrcforge-model-part-worker-handle-snapshot-v1\0";
    const WORKER_HANDLE_SECURITY_DOMAIN: &[u8] = b"vrcforge-model-part-worker-handle-security-v1\0";
    const VERIFIED_SCENARIO_START_CONTRACT_DOMAIN: &[u8] =
        b"vrcforge-verified-scenario-start-contract-v1\0";
    const VERIFIED_SCENARIO_START_CONTRACT_INVALID: &str =
        "authority_model_part_start_contract_invalid";
    const DRIVER_START_EXECUTABLE_INDEX: usize = 0;
    const BRIDGE_LAUNCHER_START_EXECUTABLE_INDEX: usize = 4;
    const MAX_SCENARIO_HANDLE_BYTES: u64 = 512 * 1024 * 1024;
    const MUTATING_SCENARIO_HANDLE_ACCESS: u32 = DELETE
        | WRITE_DAC
        | WRITE_OWNER
        | FILE_WRITE_DATA
        | FILE_APPEND_DATA
        | FILE_WRITE_EA
        | FILE_DELETE_CHILD
        | FILE_WRITE_ATTRIBUTES;
    const GENERIC_ACCESS_MASK: u32 = 0xf000_0000;
    const EXACT_READ_ONLY_SCENARIO_HANDLE_ACCESS: u32 =
        FILE_READ_DATA | FILE_READ_EA | FILE_READ_ATTRIBUTES | READ_CONTROL | SYNCHRONIZE;

    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    pub enum ScenarioHandleBundleState {
        Pending,
        Consuming,
        Active,
        Burned,
    }

    #[derive(Debug)]
    pub struct PendingScenarioHandleBundle {
        state: Arc<AtomicU8>,
    }

    #[derive(Debug)]
    struct ConsumingScenarioHandleBundle {
        state: Arc<AtomicU8>,
        tokens: ExternalModelPartHandleTokens,
        completed: bool,
    }

    /// One-use, service-owned admission of the six peer-supplied scenario
    /// files. Driver and bridge-launcher are deliberately absent: only the
    /// authenticated FinalCommit boundary may supply those protected roots.
    pub struct ValidatedExternalScenarioHandleBundle {
        state: Arc<AtomicU8>,
        files: Option<[File; EXTERNAL_MODEL_PART_HANDLE_COUNT]>,
        transferred: bool,
    }

    pub struct ActiveScenarioHandleBundle {
        state: Arc<AtomicU8>,
        files: Option<[File; FIXED_MODEL_PART_HANDLE_COUNT]>,
        #[cfg(test)]
        drop_callback: Option<Box<dyn FnOnce() + Send + 'static>>,
    }

    /// Exact service-owned duplicates used only by the native background
    /// worker. The fixed array preserves the protocol role order and the type
    /// deliberately has no Clone implementation.
    pub(crate) struct WorkerScenarioHandleBundle {
        files: Option<[File; FIXED_MODEL_PART_HANDLE_COUNT]>,
        #[cfg(test)]
        drop_observer: Option<Arc<std::sync::atomic::AtomicUsize>>,
        #[cfg(test)]
        drop_callback: Option<Box<dyn FnOnce() + Send + 'static>>,
    }

    /// Borrowed proof that both service-owned handle sets still match the
    /// exact prepare snapshot. It is intentionally non-Clone and can only be
    /// constructed by validating the two live owners together.
    pub(crate) struct VerifiedScenarioStartCapability<'a> {
        roles: &'static [&'static str; FIXED_MODEL_PART_HANDLE_COUNT],
        snapshot_digest: &'a [u8; 32],
        original_files: [&'a File; FIXED_MODEL_PART_HANDLE_COUNT],
        worker_files: [&'a File; FIXED_MODEL_PART_HANDLE_COUNT],
    }

    /// Owned, context-bound authorization for exactly one native start.
    ///
    /// This type deliberately has no `Clone` or public constructor. It can
    /// only be minted while the original and worker handle sets have just
    /// been validated together against the same immutable snapshot. The
    /// admission/background owners retain all live files through terminal;
    /// this value additionally owns the two exact executable duplicates that
    /// cross the native supervisor seam.
    pub(crate) struct VerifiedScenarioStartContract {
        snapshot_digest: [u8; 32],
        prepared_receipt_digest: [u8; 32],
        policy_snapshot_digest: [u8; 32],
        binding_digest: [u8; 32],
        driver: HeldScenarioStartExecutable,
        bridge_launcher: HeldScenarioStartExecutable,
    }

    struct HeldScenarioStartExecutable {
        role_index: usize,
        file: File,
        expected: ScenarioHandleSnapshot,
        require_immutable_access: bool,
    }

    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    pub(crate) enum ScenarioStartExecutableRole {
        Driver,
        BridgeLauncher,
    }

    /// Copy-only proof that the suspended process image was opened from the
    /// exact executable object retained by the one-use start contract.
    ///
    /// Production construction intentionally remains private to this module.
    /// The current Windows adapter is closed; a future creator must perform
    /// native create/readback inside this boundary instead of receiving a
    /// clonable `File` or raw handle.
    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    pub(crate) struct VerifiedScenarioExecutableCreateBinding {
        role: ScenarioStartExecutableRole,
        start_contract_digest: [u8; 32],
        source_object_identity_digest: [u8; 32],
        process_image_object_identity_digest: [u8; 32],
        process_image_receipt_identity_digest: [u8; 32],
        path_digest: [u8; 32],
        content_digest: [u8; 32],
        exact_object_comparison_performed: bool,
        same_kernel_object: bool,
        process_image_handle_held: bool,
        retained_start_handle_duplicate: bool,
        binding_digest: [u8; 32],
    }

    /// Pre-create view over a held executable object. The supervisor creates
    /// this immediately before the native API call, retains it across that
    /// call, and revalidates the same object immediately afterward.
    pub(crate) struct VerifiedScenarioExecutableLaunch<'a> {
        role: ScenarioStartExecutableRole,
        file: &'a File,
        expected: &'a ScenarioHandleSnapshot,
        require_immutable_access: bool,
        resolved_path: PathBuf,
        start_contract_digest: [u8; 32],
    }

    #[derive(Clone, PartialEq, Eq)]
    struct ScenarioHandleSnapshot {
        identity: StableFileIdentity,
        path: PathBuf,
        path_digest: [u8; 32],
        content_digest: [u8; 32],
        security_digest: [u8; 32],
        granted_access: u32,
    }

    #[derive(Clone, PartialEq, Eq)]
    pub(crate) struct FixedScenarioHandleSnapshot {
        roles: [ScenarioHandleSnapshot; FIXED_MODEL_PART_HANDLE_COUNT],
        digest: [u8; 32],
    }

    impl fmt::Debug for ActiveScenarioHandleBundle {
        fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
            formatter
                .debug_struct("ActiveScenarioHandleBundle")
                .field("roles", &FIXED_MODEL_PART_HANDLE_ROLES)
                .finish_non_exhaustive()
        }
    }

    impl fmt::Debug for ValidatedExternalScenarioHandleBundle {
        fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
            formatter
                .debug_struct("ValidatedExternalScenarioHandleBundle")
                .field("roles", &EXTERNAL_MODEL_PART_HANDLE_ROLES)
                .finish_non_exhaustive()
        }
    }

    impl fmt::Debug for WorkerScenarioHandleBundle {
        fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
            formatter
                .debug_struct("WorkerScenarioHandleBundle")
                .field("roles", &FIXED_MODEL_PART_HANDLE_ROLES)
                .finish_non_exhaustive()
        }
    }

    impl fmt::Debug for FixedScenarioHandleSnapshot {
        fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
            formatter
                .debug_struct("FixedScenarioHandleSnapshot")
                .field("roles", &FIXED_MODEL_PART_HANDLE_ROLES)
                .field("digest", &self.digest)
                .finish_non_exhaustive()
        }
    }

    impl fmt::Debug for VerifiedScenarioStartCapability<'_> {
        fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
            formatter
                .debug_struct("VerifiedScenarioStartCapability")
                .field("roles", self.roles)
                .field("snapshot_digest", self.snapshot_digest)
                .finish_non_exhaustive()
        }
    }

    impl fmt::Debug for VerifiedScenarioStartContract {
        fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
            formatter
                .debug_struct("VerifiedScenarioStartContract")
                .field("snapshot_digest", &self.snapshot_digest)
                .field("binding_digest", &self.binding_digest)
                .finish_non_exhaustive()
        }
    }

    impl fmt::Debug for VerifiedScenarioExecutableLaunch<'_> {
        fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
            formatter
                .debug_struct("VerifiedScenarioExecutableLaunch")
                .field("role", &self.role)
                .field("resolved_path", &self.resolved_path)
                .field("start_contract_digest", &self.start_contract_digest)
                .finish_non_exhaustive()
        }
    }

    impl Default for PendingScenarioHandleBundle {
        fn default() -> Self {
            Self {
                state: Arc::new(AtomicU8::new(SCENARIO_HANDLE_STATE_PENDING)),
            }
        }
    }

    impl PendingScenarioHandleBundle {
        pub fn state(&self) -> ScenarioHandleBundleState {
            scenario_handle_state(self.state.load(Ordering::Acquire))
        }

        fn begin(
            &self,
            tokens: ExternalModelPartHandleTokens,
        ) -> Result<ConsumingScenarioHandleBundle, AuthorityPipeError> {
            self.state
                .compare_exchange(
                    SCENARIO_HANDLE_STATE_PENDING,
                    SCENARIO_HANDLE_STATE_CONSUMING,
                    Ordering::AcqRel,
                    Ordering::Acquire,
                )
                .map_err(|_| {
                    AuthorityPipeError::new(
                        "authority_model_part_handle_capability_already_consumed",
                    )
                })?;
            Ok(ConsumingScenarioHandleBundle {
                state: Arc::clone(&self.state),
                tokens,
                completed: false,
            })
        }

        pub fn burn(&self) -> Result<(), AuthorityPipeError> {
            self.state
                .compare_exchange(
                    SCENARIO_HANDLE_STATE_PENDING,
                    SCENARIO_HANDLE_STATE_BURNED,
                    Ordering::AcqRel,
                    Ordering::Acquire,
                )
                .map(|_| ())
                .map_err(|_| {
                    AuthorityPipeError::new(
                        "authority_model_part_handle_capability_already_consumed",
                    )
                })
        }
    }

    impl Drop for PendingScenarioHandleBundle {
        fn drop(&mut self) {
            let _ = self.state.compare_exchange(
                SCENARIO_HANDLE_STATE_PENDING,
                SCENARIO_HANDLE_STATE_BURNED,
                Ordering::AcqRel,
                Ordering::Acquire,
            );
        }
    }

    impl ConsumingScenarioHandleBundle {
        fn tokens(&self) -> ExternalModelPartHandleTokens {
            self.tokens
        }

        fn activate(
            mut self,
            files: [File; EXTERNAL_MODEL_PART_HANDLE_COUNT],
        ) -> Result<ValidatedExternalScenarioHandleBundle, AuthorityPipeError> {
            self.state
                .compare_exchange(
                    SCENARIO_HANDLE_STATE_CONSUMING,
                    SCENARIO_HANDLE_STATE_ACTIVE,
                    Ordering::AcqRel,
                    Ordering::Acquire,
                )
                .map_err(|_| {
                    AuthorityPipeError::new("authority_model_part_handle_capability_state_invalid")
                })?;
            self.completed = true;
            Ok(ValidatedExternalScenarioHandleBundle {
                state: Arc::clone(&self.state),
                files: Some(files),
                transferred: false,
            })
        }
    }

    impl Drop for ConsumingScenarioHandleBundle {
        fn drop(&mut self) {
            if !self.completed {
                self.state
                    .store(SCENARIO_HANDLE_STATE_BURNED, Ordering::Release);
            }
        }
    }

    impl ValidatedExternalScenarioHandleBundle {
        pub fn state(&self) -> ScenarioHandleBundleState {
            scenario_handle_state(self.state.load(Ordering::Acquire))
        }

        pub(crate) fn compose_with_protected_roots(
            mut self,
            protected: [File; 2],
        ) -> Result<ActiveScenarioHandleBundle, AuthorityPipeError> {
            if self.state() != ScenarioHandleBundleState::Active {
                return Err(AuthorityPipeError::new(
                    "authority_model_part_handle_capability_state_invalid",
                ));
            }
            let external = self.files.take().ok_or_else(|| {
                AuthorityPipeError::new("authority_external_model_part_handle_set_incomplete")
            })?;
            let [driver, bridge_launcher] = protected;
            let [desktop, backend, unity, bridge_listener, fixture_contract, fixture_baseline] =
                external;
            let files = [
                driver,
                desktop,
                backend,
                unity,
                bridge_launcher,
                bridge_listener,
                fixture_contract,
                fixture_baseline,
            ];
            let [driver, desktop, backend, unity, bridge_launcher, bridge_listener, fixture_contract, fixture_baseline] =
                &files;
            let file_refs = [
                driver,
                desktop,
                backend,
                unity,
                bridge_launcher,
                bridge_listener,
                fixture_contract,
                fixture_baseline,
            ];
            let snapshot = FixedScenarioHandleSnapshot::capture(file_refs, true)?;
            snapshot.validate(file_refs, true)?;
            self.transferred = true;
            Ok(ActiveScenarioHandleBundle {
                state: Arc::clone(&self.state),
                files: Some(files),
                #[cfg(test)]
                drop_callback: None,
            })
        }

        #[cfg(test)]
        pub(crate) fn files(&self) -> [&File; EXTERNAL_MODEL_PART_HANDLE_COUNT] {
            let files = self
                .files
                .as_ref()
                .expect("validated external scenario bundle owns its files");
            let [desktop, backend, unity, bridge_listener, fixture_contract, fixture_baseline] =
                files;
            [
                desktop,
                backend,
                unity,
                bridge_listener,
                fixture_contract,
                fixture_baseline,
            ]
        }
    }

    impl Drop for ValidatedExternalScenarioHandleBundle {
        fn drop(&mut self) {
            drop(self.files.take());
            if !self.transferred {
                self.state
                    .store(SCENARIO_HANDLE_STATE_BURNED, Ordering::Release);
            }
        }
    }

    impl ActiveScenarioHandleBundle {
        pub fn state(&self) -> ScenarioHandleBundleState {
            scenario_handle_state(self.state.load(Ordering::Acquire))
        }

        pub fn files(&self) -> [&File; FIXED_MODEL_PART_HANDLE_COUNT] {
            let files = self
                .files
                .as_ref()
                .expect("active scenario handle bundle owns its files");
            let [driver, desktop, backend, unity, bridge_launcher, bridge_listener, fixture_contract, fixture_baseline] =
                files;
            [
                driver,
                desktop,
                backend,
                unity,
                bridge_launcher,
                bridge_listener,
                fixture_contract,
                fixture_baseline,
            ]
        }

        pub(crate) fn capture_prepare_snapshot(
            &self,
        ) -> Result<FixedScenarioHandleSnapshot, AuthorityPipeError> {
            FixedScenarioHandleSnapshot::capture(self.files(), true)
        }

        pub(crate) fn validate_snapshot(
            &self,
            expected: &FixedScenarioHandleSnapshot,
        ) -> Result<(), AuthorityPipeError> {
            expected.validate(self.files(), true)
        }

        pub(crate) fn verified_start_capability<'a>(
            &'a self,
            worker: &'a WorkerScenarioHandleBundle,
            expected: &'a FixedScenarioHandleSnapshot,
        ) -> Result<VerifiedScenarioStartCapability<'a>, AuthorityPipeError> {
            self.validate_snapshot(expected)?;
            worker.validate_snapshot(expected)?;
            Ok(VerifiedScenarioStartCapability {
                roles: &FIXED_MODEL_PART_HANDLE_ROLES,
                snapshot_digest: expected.digest(),
                original_files: self.files(),
                worker_files: worker.files(),
            })
        }

        pub(crate) fn try_clone_for_worker(
            &self,
        ) -> Result<WorkerScenarioHandleBundle, AuthorityPipeError> {
            self.try_clone_for_worker_with(true, |_, file| {
                reopen_scenario_file_object_read_only(file)
            })
        }

        fn try_clone_for_worker_with<F>(
            &self,
            require_immutable_access: bool,
            mut clone_file: F,
        ) -> Result<WorkerScenarioHandleBundle, AuthorityPipeError>
        where
            F: FnMut(usize, &File) -> Result<File, AuthorityPipeError>,
        {
            let sources = self.files();
            let mut identities = BTreeSet::new();
            let mut cloned = Vec::with_capacity(FIXED_MODEL_PART_HANDLE_COUNT);
            for (index, source) in sources.into_iter().enumerate() {
                let source_before =
                    observe_scenario_handle(index, source, require_immutable_access)?;
                let duplicate = clone_file(index, source)?;
                let duplicate_snapshot =
                    observe_scenario_handle(index, &duplicate, require_immutable_access)?;
                let source_after =
                    observe_scenario_handle(index, source, require_immutable_access)?;
                if source_before != duplicate_snapshot
                    || source_before != source_after
                    || !identities.insert((
                        duplicate_snapshot.identity.volume_serial_number,
                        duplicate_snapshot.identity.file_index,
                    ))
                {
                    return Err(AuthorityPipeError::new(WORKER_HANDLE_BINDING_MISMATCH));
                }
                cloned.push(duplicate);
            }
            let files = cloned
                .try_into()
                .map_err(|_| AuthorityPipeError::new(WORKER_HANDLE_BINDING_MISMATCH))?;
            Ok(WorkerScenarioHandleBundle {
                files: Some(files),
                #[cfg(test)]
                drop_observer: None,
                #[cfg(test)]
                drop_callback: None,
            })
        }

        #[cfg(test)]
        pub(crate) fn from_test_files(files: [File; FIXED_MODEL_PART_HANDLE_COUNT]) -> Self {
            Self {
                state: Arc::new(AtomicU8::new(SCENARIO_HANDLE_STATE_ACTIVE)),
                files: Some(files),
                drop_callback: None,
            }
        }

        #[cfg(test)]
        pub(crate) fn set_drop_callback_for_test(
            &mut self,
            drop_callback: Box<dyn FnOnce() + Send + 'static>,
        ) {
            self.drop_callback = Some(drop_callback);
        }

        #[cfg(test)]
        pub(super) fn try_clone_for_worker_with_test<F>(
            &self,
            clone_file: F,
        ) -> Result<WorkerScenarioHandleBundle, AuthorityPipeError>
        where
            F: FnMut(usize, &File) -> io::Result<File>,
        {
            let mut clone_file = clone_file;
            self.try_clone_for_worker_with(true, |index, file| {
                clone_file(index, file)
                    .map_err(|_| AuthorityPipeError::new(WORKER_HANDLE_CLONE_FAILED))
            })
        }

        #[cfg(test)]
        pub(super) fn clone_for_worker_relaxed_for_test(
            &self,
        ) -> Result<WorkerScenarioHandleBundle, AuthorityPipeError> {
            self.try_clone_for_worker_with(false, |_, file| {
                file.try_clone()
                    .map_err(|_| AuthorityPipeError::new(WORKER_HANDLE_CLONE_FAILED))
            })
        }

        #[cfg(test)]
        pub(crate) fn capture_relaxed_snapshot_for_test(
            &self,
        ) -> Result<FixedScenarioHandleSnapshot, AuthorityPipeError> {
            FixedScenarioHandleSnapshot::capture(self.files(), false)
        }

        #[cfg(test)]
        pub(crate) fn validate_relaxed_snapshot_for_test(
            &self,
            expected: &FixedScenarioHandleSnapshot,
        ) -> Result<(), AuthorityPipeError> {
            expected.validate(self.files(), false)
        }
    }

    impl WorkerScenarioHandleBundle {
        pub(crate) fn files(&self) -> [&File; FIXED_MODEL_PART_HANDLE_COUNT] {
            let files = self
                .files
                .as_ref()
                .expect("worker scenario handle bundle owns its files");
            let [driver, desktop, backend, unity, bridge_launcher, bridge_listener, fixture_contract, fixture_baseline] =
                files;
            [
                driver,
                desktop,
                backend,
                unity,
                bridge_launcher,
                bridge_listener,
                fixture_contract,
                fixture_baseline,
            ]
        }

        pub(crate) fn validate_snapshot(
            &self,
            expected: &FixedScenarioHandleSnapshot,
        ) -> Result<(), AuthorityPipeError> {
            expected.validate(self.files(), true)
        }

        #[cfg(test)]
        pub(crate) fn verified_start_capability_for_test(
            &self,
        ) -> VerifiedScenarioStartCapability<'_> {
            static TEST_SNAPSHOT_DIGEST: [u8; 32] = [0x5a; 32];
            VerifiedScenarioStartCapability {
                roles: &FIXED_MODEL_PART_HANDLE_ROLES,
                snapshot_digest: &TEST_SNAPSHOT_DIGEST,
                original_files: self.files(),
                worker_files: self.files(),
            }
        }

        #[cfg(test)]
        pub(crate) fn from_test_files(
            files: [File; FIXED_MODEL_PART_HANDLE_COUNT],
            drop_observer: Arc<std::sync::atomic::AtomicUsize>,
        ) -> Self {
            Self {
                files: Some(files),
                drop_observer: Some(drop_observer),
                drop_callback: None,
            }
        }

        #[cfg(test)]
        pub(crate) fn set_drop_callback_for_test(
            &mut self,
            drop_callback: Box<dyn FnOnce() + Send + 'static>,
        ) {
            self.drop_callback = Some(drop_callback);
        }

        #[cfg(test)]
        pub(crate) fn validate_relaxed_snapshot_for_test(
            &self,
            expected: &FixedScenarioHandleSnapshot,
        ) -> Result<(), AuthorityPipeError> {
            expected.validate(self.files(), false)
        }
    }

    impl FixedScenarioHandleSnapshot {
        fn capture(
            files: [&File; FIXED_MODEL_PART_HANDLE_COUNT],
            require_immutable_access: bool,
        ) -> Result<Self, AuthorityPipeError> {
            let mut roles = Vec::with_capacity(FIXED_MODEL_PART_HANDLE_COUNT);
            let mut identities = BTreeSet::new();
            for (index, file) in files.into_iter().enumerate() {
                let observed = observe_scenario_handle(index, file, require_immutable_access)?;
                if !identities.insert((
                    observed.identity.volume_serial_number,
                    observed.identity.file_index,
                )) {
                    return Err(AuthorityPipeError::new(
                        "authority_model_part_handle_identity_alias",
                    ));
                }
                roles.push(observed);
            }
            let roles: [ScenarioHandleSnapshot; FIXED_MODEL_PART_HANDLE_COUNT] =
                roles
                    .try_into()
                    .map_err(|_| AuthorityPipeError::new(WORKER_HANDLE_SNAPSHOT_INVALID))?;
            let digest = fixed_scenario_snapshot_digest(&roles);
            Ok(Self { roles, digest })
        }

        fn validate(
            &self,
            files: [&File; FIXED_MODEL_PART_HANDLE_COUNT],
            require_immutable_access: bool,
        ) -> Result<(), AuthorityPipeError> {
            let observed = Self::capture(files, require_immutable_access)?;
            if self.digest != fixed_scenario_snapshot_digest(&self.roles)
                || &observed != self
                || observed.digest != fixed_scenario_snapshot_digest(&observed.roles)
            {
                return Err(AuthorityPipeError::new(WORKER_HANDLE_SNAPSHOT_MISMATCH));
            }
            Ok(())
        }

        pub(crate) fn digest(&self) -> &[u8; 32] {
            &self.digest
        }

        #[cfg(test)]
        pub(crate) fn validate_files_for_test(
            &self,
            files: [&File; FIXED_MODEL_PART_HANDLE_COUNT],
        ) -> Result<(), AuthorityPipeError> {
            self.validate(files, true)
        }
    }

    impl<'a> VerifiedScenarioStartCapability<'a> {
        pub(crate) fn roles(&self) -> &'static [&'static str; FIXED_MODEL_PART_HANDLE_COUNT] {
            self.roles
        }

        pub(crate) fn snapshot_digest(&self) -> &'a [u8; 32] {
            self.snapshot_digest
        }

        pub(crate) fn original_files(&self) -> [&'a File; FIXED_MODEL_PART_HANDLE_COUNT] {
            self.original_files
        }

        pub(crate) fn worker_files(&self) -> [&'a File; FIXED_MODEL_PART_HANDLE_COUNT] {
            self.worker_files
        }

        pub(crate) fn into_owned_contract(
            self,
            prepared_receipt_digest: [u8; 32],
            policy_snapshot_digest: [u8; 32],
        ) -> Result<VerifiedScenarioStartContract, AuthorityPipeError> {
            self.into_owned_contract_with(
                prepared_receipt_digest,
                policy_snapshot_digest,
                |_, file| reopen_scenario_file_object_read_only(file),
            )
        }

        fn into_owned_contract_with<F>(
            self,
            prepared_receipt_digest: [u8; 32],
            policy_snapshot_digest: [u8; 32],
            mut reopen_file: F,
        ) -> Result<VerifiedScenarioStartContract, AuthorityPipeError>
        where
            F: FnMut(usize, &File) -> Result<File, AuthorityPipeError>,
        {
            let driver = held_start_executable_from_verified_pair(
                self.original_files[DRIVER_START_EXECUTABLE_INDEX],
                self.worker_files[DRIVER_START_EXECUTABLE_INDEX],
                DRIVER_START_EXECUTABLE_INDEX,
                &mut reopen_file,
            )?;
            let bridge_launcher = held_start_executable_from_verified_pair(
                self.original_files[BRIDGE_LAUNCHER_START_EXECUTABLE_INDEX],
                self.worker_files[BRIDGE_LAUNCHER_START_EXECUTABLE_INDEX],
                BRIDGE_LAUNCHER_START_EXECUTABLE_INDEX,
                &mut reopen_file,
            )?;
            verified_start_contract_from_digests(
                *self.snapshot_digest,
                prepared_receipt_digest,
                policy_snapshot_digest,
                driver,
                bridge_launcher,
            )
        }

        #[cfg(test)]
        pub(super) fn into_owned_contract_with_test<F>(
            self,
            prepared_receipt_digest: [u8; 32],
            policy_snapshot_digest: [u8; 32],
            mut reopen_file: F,
        ) -> Result<VerifiedScenarioStartContract, AuthorityPipeError>
        where
            F: FnMut(usize, &File) -> io::Result<File>,
        {
            self.into_owned_contract_with(
                prepared_receipt_digest,
                policy_snapshot_digest,
                |index, file| {
                    reopen_file(index, file)
                        .map_err(|_| AuthorityPipeError::new(WORKER_HANDLE_CLONE_FAILED))
                },
            )
        }
    }

    impl VerifiedScenarioStartContract {
        pub(crate) fn snapshot_digest(&self) -> &[u8; 32] {
            &self.snapshot_digest
        }

        pub(crate) fn binding_digest(&self) -> [u8; 32] {
            self.binding_digest
        }

        pub(crate) fn verifies_for(
            &self,
            prepared_receipt_digest: &[u8; 32],
            policy_snapshot_digest: &[u8; 32],
        ) -> bool {
            if &self.prepared_receipt_digest != prepared_receipt_digest
                || &self.policy_snapshot_digest != policy_snapshot_digest
                || self.binding_digest.iter().all(|byte| *byte == 0)
                || !held_start_executables_are_canonical(&self.driver, &self.bridge_launcher)
                || self.binding_digest
                    != recompute_verified_start_contract_binding_digest(
                        self.snapshot_digest,
                        self.prepared_receipt_digest,
                        self.policy_snapshot_digest,
                        &self.driver,
                        &self.bridge_launcher,
                    )
            {
                return false;
            }
            revalidate_held_start_executable(&self.driver).is_ok()
                && revalidate_held_start_executable(&self.bridge_launcher).is_ok()
        }

        #[cfg(test)]
        pub(crate) fn corrupt_binding_digest_for_test(&mut self) {
            self.binding_digest[0] ^= 0xff;
        }

        #[cfg(test)]
        pub(crate) fn executable_stream_positions_for_test(
            &self,
        ) -> Result<[u64; 2], AuthorityPipeError> {
            Ok([
                scenario_handle_stream_position_for_test(&self.driver.file)?,
                scenario_handle_stream_position_for_test(&self.bridge_launcher.file)?,
            ])
        }

        #[cfg(test)]
        pub(crate) fn set_executable_stream_positions_for_test(
            &self,
            positions: [u64; 2],
        ) -> Result<(), AuthorityPipeError> {
            set_scenario_handle_stream_position_for_test(&self.driver.file, positions[0])?;
            set_scenario_handle_stream_position_for_test(&self.bridge_launcher.file, positions[1])
        }

        #[cfg(test)]
        pub(crate) fn for_test_from_files(
            driver: File,
            bridge_launcher: File,
            prepared_receipt_digest: [u8; 32],
            policy_snapshot_digest: [u8; 32],
        ) -> Result<Self, AuthorityPipeError> {
            let driver =
                held_start_executable_from_file_for_test(driver, DRIVER_START_EXECUTABLE_INDEX)?;
            let bridge_launcher = held_start_executable_from_file_for_test(
                bridge_launcher,
                BRIDGE_LAUNCHER_START_EXECUTABLE_INDEX,
            )?;
            verified_start_contract_from_digests(
                [0x5a; 32],
                prepared_receipt_digest,
                policy_snapshot_digest,
                driver,
                bridge_launcher,
            )
        }

        pub(crate) fn prepare_executable_launch(
            &self,
            role: ScenarioStartExecutableRole,
        ) -> Result<VerifiedScenarioExecutableLaunch<'_>, AuthorityPipeError> {
            let held = match role {
                ScenarioStartExecutableRole::Driver => &self.driver,
                ScenarioStartExecutableRole::BridgeLauncher => &self.bridge_launcher,
            };
            let observed = revalidate_held_start_executable(held)?;
            Ok(VerifiedScenarioExecutableLaunch {
                role,
                file: &held.file,
                expected: &held.expected,
                require_immutable_access: held.require_immutable_access,
                resolved_path: observed.path,
                start_contract_digest: self.binding_digest,
            })
        }
    }

    impl VerifiedScenarioExecutableLaunch<'_> {
        pub(crate) fn role(&self) -> ScenarioStartExecutableRole {
            self.role
        }

        pub(crate) fn resolved_path(&self) -> &Path {
            &self.resolved_path
        }

        pub(crate) fn start_contract_digest(&self) -> [u8; 32] {
            self.start_contract_digest
        }

        pub(crate) fn expected_content_digest(&self) -> [u8; 32] {
            self.expected.content_digest
        }

        fn revalidate_after_create(&self) -> Result<(), AuthorityPipeError> {
            let observed = observe_scenario_handle(
                self.expected_role_index(),
                self.file,
                self.require_immutable_access,
            )?;
            if &observed != self.expected || observed.path.as_path() != self.resolved_path.as_path()
            {
                return Err(AuthorityPipeError::new(WORKER_HANDLE_SNAPSHOT_MISMATCH));
            }
            Ok(())
        }

        pub(crate) fn validate_created_process_image(
            &self,
            binding: &VerifiedScenarioExecutableCreateBinding,
            process_image_receipt_identity_digest: [u8; 32],
        ) -> Result<(), AuthorityPipeError> {
            self.revalidate_after_create()?;
            let source_object_identity_digest = self.expected.identity.binding_digest();
            if binding.role != self.role
                || binding.start_contract_digest != self.start_contract_digest
                || binding.source_object_identity_digest != source_object_identity_digest
                || binding.process_image_object_identity_digest != source_object_identity_digest
                || binding.process_image_receipt_identity_digest
                    != process_image_receipt_identity_digest
                || binding.path_digest != self.expected.path_digest
                || binding.content_digest != self.expected.content_digest
                || !binding.exact_object_comparison_performed
                || !binding.same_kernel_object
                || !binding.process_image_handle_held
                || binding.retained_start_handle_duplicate
                || process_image_receipt_identity_digest
                    .iter()
                    .all(|byte| *byte == 0)
                || binding.binding_digest != recompute_executable_create_binding_digest(binding)
            {
                return Err(AuthorityPipeError::new(WORKER_HANDLE_SNAPSHOT_MISMATCH));
            }
            Ok(())
        }

        pub(crate) fn bind_created_process_image(
            &self,
            process_image: &File,
            process_image_receipt_identity_digest: [u8; 32],
        ) -> Result<VerifiedScenarioExecutableCreateBinding, AuthorityPipeError> {
            self.create_binding(process_image, process_image_receipt_identity_digest, false)
        }

        #[cfg(test)]
        pub(crate) fn create_binding_for_test(
            &self,
            process_image: &File,
            process_image_receipt_identity_digest: [u8; 32],
            retained_start_handle_duplicate: bool,
        ) -> Result<VerifiedScenarioExecutableCreateBinding, AuthorityPipeError> {
            self.create_binding(
                process_image,
                process_image_receipt_identity_digest,
                retained_start_handle_duplicate,
            )
        }

        fn create_binding(
            &self,
            process_image: &File,
            process_image_receipt_identity_digest: [u8; 32],
            retained_start_handle_duplicate: bool,
        ) -> Result<VerifiedScenarioExecutableCreateBinding, AuthorityPipeError> {
            self.revalidate_after_create()?;
            let process_image_observation =
                observe_scenario_handle(self.expected_role_index(), process_image, false)?;
            let source_object_identity_digest = self.expected.identity.binding_digest();
            let process_image_object_identity_digest =
                process_image_observation.identity.binding_digest();
            let mut binding = VerifiedScenarioExecutableCreateBinding {
                role: self.role,
                start_contract_digest: self.start_contract_digest,
                source_object_identity_digest,
                process_image_object_identity_digest,
                process_image_receipt_identity_digest,
                path_digest: process_image_observation.path_digest,
                content_digest: process_image_observation.content_digest,
                exact_object_comparison_performed: true,
                same_kernel_object: process_image_observation.identity == self.expected.identity,
                process_image_handle_held: true,
                retained_start_handle_duplicate,
                binding_digest: [0; 32],
            };
            binding.binding_digest = recompute_executable_create_binding_digest(&binding);
            Ok(binding)
        }

        fn expected_role_index(&self) -> usize {
            match self.role {
                ScenarioStartExecutableRole::Driver => DRIVER_START_EXECUTABLE_INDEX,
                ScenarioStartExecutableRole::BridgeLauncher => {
                    BRIDGE_LAUNCHER_START_EXECUTABLE_INDEX
                }
            }
        }
    }

    impl VerifiedScenarioExecutableCreateBinding {
        pub(crate) fn binding_digest(&self) -> [u8; 32] {
            self.binding_digest
        }

        pub(crate) fn process_image_receipt_identity_digest(&self) -> [u8; 32] {
            self.process_image_receipt_identity_digest
        }

        pub(crate) fn verifies_persisted_receipt(
            &self,
            role: ScenarioStartExecutableRole,
            start_contract_digest: [u8; 32],
            process_image_receipt_identity_digest: [u8; 32],
        ) -> bool {
            self.role == role
                && self.start_contract_digest == start_contract_digest
                && self.source_object_identity_digest == self.process_image_object_identity_digest
                && self.process_image_receipt_identity_digest
                    == process_image_receipt_identity_digest
                && !self.path_digest.iter().all(|byte| *byte == 0)
                && !self.content_digest.iter().all(|byte| *byte == 0)
                && self.exact_object_comparison_performed
                && self.same_kernel_object
                && self.process_image_handle_held
                && !self.retained_start_handle_duplicate
                && !process_image_receipt_identity_digest
                    .iter()
                    .all(|byte| *byte == 0)
                && self.binding_digest == recompute_executable_create_binding_digest(self)
        }

        #[cfg(test)]
        pub(crate) fn valid_for_launch_for_test(
            launch: &VerifiedScenarioExecutableLaunch<'_>,
            process_image_receipt_identity_digest: [u8; 32],
        ) -> Result<Self, AuthorityPipeError> {
            launch.create_binding_for_test(
                launch.file,
                process_image_receipt_identity_digest,
                false,
            )
        }
    }

    fn recompute_executable_create_binding_digest(
        binding: &VerifiedScenarioExecutableCreateBinding,
    ) -> [u8; 32] {
        let mut digest = Sha256::new();
        digest.update(b"vrcforge-authority-executable-create-binding-v1\0");
        digest.update([match binding.role {
            ScenarioStartExecutableRole::Driver => 1,
            ScenarioStartExecutableRole::BridgeLauncher => 2,
        }]);
        digest.update(binding.start_contract_digest);
        digest.update(binding.source_object_identity_digest);
        digest.update(binding.process_image_object_identity_digest);
        digest.update(binding.process_image_receipt_identity_digest);
        digest.update(binding.path_digest);
        digest.update(binding.content_digest);
        digest.update([
            u8::from(binding.exact_object_comparison_performed),
            u8::from(binding.same_kernel_object),
            u8::from(binding.process_image_handle_held),
            u8::from(binding.retained_start_handle_duplicate),
        ]);
        digest.finalize().into()
    }

    fn verified_start_contract_from_digests(
        snapshot_digest: [u8; 32],
        prepared_receipt_digest: [u8; 32],
        policy_snapshot_digest: [u8; 32],
        driver: HeldScenarioStartExecutable,
        bridge_launcher: HeldScenarioStartExecutable,
    ) -> Result<VerifiedScenarioStartContract, AuthorityPipeError> {
        if snapshot_digest.iter().all(|byte| *byte == 0)
            || prepared_receipt_digest.iter().all(|byte| *byte == 0)
            || policy_snapshot_digest.iter().all(|byte| *byte == 0)
        {
            return Err(AuthorityPipeError::new(
                VERIFIED_SCENARIO_START_CONTRACT_INVALID,
            ));
        }

        if !held_start_executables_are_canonical(&driver, &bridge_launcher) {
            return Err(AuthorityPipeError::new(
                VERIFIED_SCENARIO_START_CONTRACT_INVALID,
            ));
        }
        let binding_digest = recompute_verified_start_contract_binding_digest(
            snapshot_digest,
            prepared_receipt_digest,
            policy_snapshot_digest,
            &driver,
            &bridge_launcher,
        );
        Ok(VerifiedScenarioStartContract {
            snapshot_digest,
            prepared_receipt_digest,
            policy_snapshot_digest,
            binding_digest,
            driver,
            bridge_launcher,
        })
    }

    fn recompute_verified_start_contract_binding_digest(
        snapshot_digest: [u8; 32],
        prepared_receipt_digest: [u8; 32],
        policy_snapshot_digest: [u8; 32],
        driver: &HeldScenarioStartExecutable,
        bridge_launcher: &HeldScenarioStartExecutable,
    ) -> [u8; 32] {
        let mut digest = Sha256::new();
        digest.update(VERIFIED_SCENARIO_START_CONTRACT_DOMAIN);
        for role in FIXED_MODEL_PART_HANDLE_ROLES {
            digest.update((role.len() as u64).to_be_bytes());
            digest.update(role.as_bytes());
        }
        digest.update(snapshot_digest);
        digest.update(prepared_receipt_digest);
        digest.update(policy_snapshot_digest);
        for executable in [driver, bridge_launcher] {
            digest.update((executable.role_index as u64).to_be_bytes());
            digest.update(executable.expected.identity.binding_digest());
            digest.update(executable.expected.path_digest);
            digest.update(executable.expected.content_digest);
            digest.update(executable.expected.security_digest);
            digest.update(executable.expected.granted_access.to_be_bytes());
        }
        digest.finalize().into()
    }

    fn held_start_executables_are_canonical(
        driver: &HeldScenarioStartExecutable,
        bridge_launcher: &HeldScenarioStartExecutable,
    ) -> bool {
        driver.role_index == DRIVER_START_EXECUTABLE_INDEX
            && bridge_launcher.role_index == BRIDGE_LAUNCHER_START_EXECUTABLE_INDEX
            && driver.expected.identity != bridge_launcher.expected.identity
            && driver.expected.path != bridge_launcher.expected.path
            && driver.expected.path_digest == scenario_path_digest(&driver.expected.path)
            && bridge_launcher.expected.path_digest
                == scenario_path_digest(&bridge_launcher.expected.path)
            && !driver.expected.content_digest.iter().all(|byte| *byte == 0)
            && !driver
                .expected
                .security_digest
                .iter()
                .all(|byte| *byte == 0)
            && !bridge_launcher
                .expected
                .content_digest
                .iter()
                .all(|byte| *byte == 0)
            && !bridge_launcher
                .expected
                .security_digest
                .iter()
                .all(|byte| *byte == 0)
            && scenario_granted_access_is_read_only(driver.expected.granted_access)
            && scenario_granted_access_is_read_only(bridge_launcher.expected.granted_access)
    }

    fn revalidate_held_start_executable(
        held: &HeldScenarioStartExecutable,
    ) -> Result<ScenarioHandleSnapshot, AuthorityPipeError> {
        let observed =
            observe_scenario_handle(held.role_index, &held.file, held.require_immutable_access)?;
        if observed != held.expected {
            return Err(AuthorityPipeError::new(WORKER_HANDLE_SNAPSHOT_MISMATCH));
        }
        Ok(observed)
    }

    fn held_start_executable_from_verified_pair<F>(
        original: &File,
        worker: &File,
        role_index: usize,
        reopen_file: &mut F,
    ) -> Result<HeldScenarioStartExecutable, AuthorityPipeError>
    where
        F: FnMut(usize, &File) -> Result<File, AuthorityPipeError>,
    {
        let original_snapshot = observe_scenario_handle(role_index, original, true)?;
        let worker_snapshot = observe_scenario_handle(role_index, worker, true)?;
        if original_snapshot != worker_snapshot {
            return Err(AuthorityPipeError::new(WORKER_HANDLE_SNAPSHOT_MISMATCH));
        }
        let duplicate = reopen_file(role_index, worker)?;
        let duplicate_snapshot = observe_scenario_handle(role_index, &duplicate, true)?;
        let original_after = observe_scenario_handle(role_index, original, true)?;
        let worker_after = observe_scenario_handle(role_index, worker, true)?;
        if duplicate_snapshot != worker_snapshot
            || original_after != original_snapshot
            || worker_after != worker_snapshot
        {
            return Err(AuthorityPipeError::new(WORKER_HANDLE_SNAPSHOT_MISMATCH));
        }
        Ok(HeldScenarioStartExecutable {
            role_index,
            file: duplicate,
            expected: duplicate_snapshot,
            require_immutable_access: true,
        })
    }

    #[cfg(test)]
    fn held_start_executable_from_file_for_test(
        file: File,
        role_index: usize,
    ) -> Result<HeldScenarioStartExecutable, AuthorityPipeError> {
        let expected = observe_scenario_handle(role_index, &file, false)?;
        Ok(HeldScenarioStartExecutable {
            role_index,
            file,
            expected,
            require_immutable_access: false,
        })
    }

    impl Drop for WorkerScenarioHandleBundle {
        fn drop(&mut self) {
            drop(self.files.take());
            #[cfg(test)]
            if let Some(observer) = &self.drop_observer {
                observer.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            }
            #[cfg(test)]
            if let Some(callback) = self.drop_callback.take() {
                callback();
            }
        }
    }

    fn worker_handle_identity(file: &File) -> Result<StableFileIdentity, AuthorityPipeError> {
        let raw = file.as_raw_handle().cast();
        if unsafe { GetFileType(raw) } != FILE_TYPE_DISK {
            return Err(AuthorityPipeError::new(WORKER_HANDLE_BINDING_MISMATCH));
        }
        let mut flags = 0u32;
        if unsafe { GetHandleInformation(raw, &mut flags) } == 0 || flags & HANDLE_FLAG_INHERIT != 0
        {
            return Err(AuthorityPipeError::new(WORKER_HANDLE_BINDING_MISMATCH));
        }
        query_file_identity(raw)
            .map_err(|_| AuthorityPipeError::new(WORKER_HANDLE_BINDING_MISMATCH))
    }

    #[repr(C)]
    #[derive(Clone, Copy)]
    struct ScenarioObjectBasicInformation {
        attributes: u32,
        granted_access: u32,
        handle_count: u32,
        pointer_count: u32,
        reserved: [u32; 10],
    }

    fn observe_scenario_handle(
        role_index: usize,
        file: &File,
        require_immutable_access: bool,
    ) -> Result<ScenarioHandleSnapshot, AuthorityPipeError> {
        if role_index >= FIXED_MODEL_PART_HANDLE_COUNT {
            return Err(AuthorityPipeError::new(WORKER_HANDLE_SNAPSHOT_INVALID));
        }
        let granted_access = scenario_handle_granted_access(file)?;
        if require_immutable_access && !scenario_granted_access_is_read_only(granted_access) {
            return Err(AuthorityPipeError::new(WORKER_HANDLE_ACCESS_INVALID));
        }
        let before = worker_handle_identity(file)?;
        if before.size == 0 || before.size > MAX_SCENARIO_HANDLE_BYTES || before.link_count != 1 {
            return Err(AuthorityPipeError::new(WORKER_HANDLE_SNAPSHOT_INVALID));
        }
        let path = scenario_handle_path(file)?;
        require_scenario_namespace_binding(&path, &before)?;
        if require_immutable_access {
            require_mutation_sharing_denied(&path)?;
        }
        let security_digest = scenario_handle_security_digest(file)?;
        let content_digest = hash_scenario_handle(file, before.size)?;
        let granted_access_after = scenario_handle_granted_access(file)?;
        let after = worker_handle_identity(file)?;
        let path_after = scenario_handle_path(file)?;
        require_scenario_namespace_binding(&path_after, &after)?;
        let security_digest_after = scenario_handle_security_digest(file)?;
        if before != after
            || path != path_after
            || granted_access != granted_access_after
            || security_digest != security_digest_after
        {
            return Err(AuthorityPipeError::new(WORKER_HANDLE_SNAPSHOT_MISMATCH));
        }
        Ok(ScenarioHandleSnapshot {
            identity: before,
            path_digest: scenario_path_digest(&path),
            path,
            content_digest,
            security_digest,
            granted_access,
        })
    }

    fn scenario_handle_granted_access(file: &File) -> Result<u32, AuthorityPipeError> {
        let mut information = unsafe { zeroed::<ScenarioObjectBasicInformation>() };
        let mut returned = 0u32;
        let status = unsafe {
            NtQueryObject(
                file.as_raw_handle().cast(),
                ObjectBasicInformation,
                (&mut information as *mut ScenarioObjectBasicInformation).cast(),
                size_of::<ScenarioObjectBasicInformation>() as u32,
                &mut returned,
            )
        };
        if status < 0 || returned < (size_of::<u32>() * 2) as u32 {
            return Err(AuthorityPipeError::new(WORKER_HANDLE_ACCESS_INVALID));
        }
        Ok(information.granted_access)
    }

    fn scenario_granted_access_is_read_only(granted_access: u32) -> bool {
        granted_access & FILE_READ_DATA != 0
            && granted_access & !EXACT_READ_ONLY_SCENARIO_HANDLE_ACCESS == 0
    }

    #[cfg(test)]
    pub(super) fn scenario_granted_access_is_read_only_for_test(granted_access: u32) -> bool {
        scenario_granted_access_is_read_only(granted_access)
    }

    fn require_read_only_scenario_handle(file: &File) -> Result<u32, AuthorityPipeError> {
        let granted_access = scenario_handle_granted_access(file)?;
        if !scenario_granted_access_is_read_only(granted_access) {
            return Err(AuthorityPipeError::new(WORKER_HANDLE_ACCESS_INVALID));
        }
        Ok(granted_access)
    }

    fn scenario_handle_security_digest(file: &File) -> Result<[u8; 32], AuthorityPipeError> {
        let projection =
            OWNER_SECURITY_INFORMATION | GROUP_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION;
        let mut descriptor = ptr::null_mut();
        let status = unsafe {
            GetSecurityInfo(
                file.as_raw_handle().cast(),
                SE_FILE_OBJECT,
                projection,
                ptr::null_mut(),
                ptr::null_mut(),
                ptr::null_mut(),
                ptr::null_mut(),
                &mut descriptor,
            )
        };
        if status != 0 || descriptor.is_null() {
            return Err(AuthorityPipeError::new(WORKER_HANDLE_SNAPSHOT_INVALID));
        }
        let descriptor = SecurityDescriptor(descriptor);
        let byte_length = unsafe { GetSecurityDescriptorLength(descriptor.0) };
        if byte_length == 0 || byte_length > 64 * 1024 {
            return Err(AuthorityPipeError::new(WORKER_HANDLE_SNAPSHOT_INVALID));
        }
        let bytes =
            unsafe { std::slice::from_raw_parts(descriptor.0.cast::<u8>(), byte_length as usize) };
        let mut digest = Sha256::new();
        digest.update(WORKER_HANDLE_SECURITY_DOMAIN);
        digest.update(projection.to_be_bytes());
        digest.update((bytes.len() as u64).to_be_bytes());
        digest.update(bytes);
        Ok(digest.finalize().into())
    }

    fn scenario_handle_path(file: &File) -> Result<PathBuf, AuthorityPipeError> {
        let mut words = vec![0u16; 32_768];
        let length = unsafe {
            GetFinalPathNameByHandleW(
                file.as_raw_handle().cast(),
                words.as_mut_ptr(),
                words.len() as u32,
                0,
            )
        } as usize;
        if length == 0 || length >= words.len() {
            return Err(AuthorityPipeError::new(WORKER_HANDLE_SNAPSHOT_INVALID));
        }
        words.truncate(length);
        if words.contains(&0) {
            return Err(AuthorityPipeError::new(WORKER_HANDLE_SNAPSHOT_INVALID));
        }
        let path = PathBuf::from(OsString::from_wide(&words));
        if path.as_os_str().is_empty() {
            return Err(AuthorityPipeError::new(WORKER_HANDLE_SNAPSHOT_INVALID));
        }
        Ok(path)
    }

    fn require_scenario_namespace_binding(
        path: &Path,
        expected: &StableFileIdentity,
    ) -> Result<(), AuthorityPipeError> {
        let path = wide_null(path.as_os_str());
        let raw = unsafe {
            CreateFileW(
                path.as_ptr(),
                0,
                FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                ptr::null(),
                OPEN_EXISTING,
                FILE_FLAG_OPEN_REPARSE_POINT,
                ptr::null_mut(),
            )
        };
        if raw == INVALID_HANDLE_VALUE {
            return Err(AuthorityPipeError::new(WORKER_HANDLE_SNAPSHOT_MISMATCH));
        }
        let file = File::from(unsafe { OwnedHandle::from_raw_handle(raw as RawHandle) });
        let observed = worker_handle_identity(&file)
            .map_err(|_| AuthorityPipeError::new(WORKER_HANDLE_SNAPSHOT_MISMATCH))?;
        if &observed != expected {
            return Err(AuthorityPipeError::new(WORKER_HANDLE_SNAPSHOT_MISMATCH));
        }
        Ok(())
    }

    fn require_mutation_sharing_denied(path: &Path) -> Result<(), AuthorityPipeError> {
        for access in [
            FILE_WRITE_DATA | FILE_APPEND_DATA | FILE_WRITE_EA | FILE_WRITE_ATTRIBUTES,
            DELETE,
        ] {
            let path = wide_null(path.as_os_str());
            let raw = unsafe {
                CreateFileW(
                    path.as_ptr(),
                    access,
                    FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                    ptr::null(),
                    OPEN_EXISTING,
                    FILE_FLAG_OPEN_REPARSE_POINT,
                    ptr::null_mut(),
                )
            };
            if raw != INVALID_HANDLE_VALUE {
                drop(unsafe { OwnedHandle::from_raw_handle(raw as RawHandle) });
                return Err(AuthorityPipeError::new(WORKER_HANDLE_SHARING_INVALID));
            }
            if unsafe { GetLastError() } != ERROR_SHARING_VIOLATION {
                return Err(AuthorityPipeError::new(WORKER_HANDLE_SHARING_INVALID));
            }
        }
        Ok(())
    }

    fn reopen_scenario_file_object_read_only(source: &File) -> Result<File, AuthorityPipeError> {
        let source_access = require_read_only_scenario_handle(source)?;
        let source_identity = worker_handle_identity(source)?;
        if source_identity.size == 0
            || source_identity.size > MAX_SCENARIO_HANDLE_BYTES
            || source_identity.link_count != 1
        {
            return Err(AuthorityPipeError::new(WORKER_HANDLE_SNAPSHOT_INVALID));
        }
        let source_path = scenario_handle_path(source)?;
        require_scenario_namespace_binding(&source_path, &source_identity)?;
        require_mutation_sharing_denied(&source_path)?;
        reopen_scenario_file_object_with_access(source, source_access, FILE_SHARE_READ)
    }

    fn reopen_scenario_reader(source: &File) -> Result<File, AuthorityPipeError> {
        let source_access = scenario_handle_granted_access(source)?;
        if source_access & FILE_READ_DATA == 0 || source_access & GENERIC_ACCESS_MASK != 0 {
            return Err(AuthorityPipeError::new(WORKER_HANDLE_ACCESS_INVALID));
        }
        let reader_access = source_access & !MUTATING_SCENARIO_HANDLE_ACCESS;
        if !scenario_granted_access_is_read_only(reader_access) {
            return Err(AuthorityPipeError::new(WORKER_HANDLE_ACCESS_INVALID));
        }
        let share_mode = if scenario_granted_access_is_read_only(source_access) {
            FILE_SHARE_READ
        } else {
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
        };
        reopen_scenario_file_object_with_access(source, reader_access, share_mode)
    }

    fn reopen_scenario_file_object_with_access(
        source: &File,
        desired_access: u32,
        share_mode: u32,
    ) -> Result<File, AuthorityPipeError> {
        if !scenario_granted_access_is_read_only(desired_access) {
            return Err(AuthorityPipeError::new(WORKER_HANDLE_ACCESS_INVALID));
        }
        let source_access_before = scenario_handle_granted_access(source)?;
        let source_identity_before = worker_handle_identity(source)?;
        if source_identity_before.size == 0
            || source_identity_before.size > MAX_SCENARIO_HANDLE_BYTES
            || source_identity_before.link_count != 1
        {
            return Err(AuthorityPipeError::new(WORKER_HANDLE_SNAPSHOT_INVALID));
        }
        let source_path_before = scenario_handle_path(source)?;
        require_scenario_namespace_binding(&source_path_before, &source_identity_before)?;
        let source_security_before = scenario_handle_security_digest(source)?;

        let raw = unsafe {
            ReOpenFile(
                source.as_raw_handle().cast(),
                desired_access,
                share_mode,
                FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_SEQUENTIAL_SCAN,
            )
        };
        if raw.is_null() || raw == INVALID_HANDLE_VALUE {
            return Err(AuthorityPipeError::new(WORKER_HANDLE_CLONE_FAILED));
        }
        // SAFETY: ReOpenFile returned a distinct owned file object. File owns
        // and closes it on every success and error path after this conversion.
        let reopened = unsafe { File::from_raw_handle(raw as RawHandle) };
        let reopened_access = require_read_only_scenario_handle(&reopened)?;
        let reopened_identity = worker_handle_identity(&reopened)?;
        if reopened_access != desired_access
            || reopened_identity.size == 0
            || reopened_identity.size > MAX_SCENARIO_HANDLE_BYTES
            || reopened_identity.link_count != 1
        {
            return Err(AuthorityPipeError::new(WORKER_HANDLE_BINDING_MISMATCH));
        }
        let reopened_path = scenario_handle_path(&reopened)?;
        require_scenario_namespace_binding(&reopened_path, &reopened_identity)?;
        if share_mode == FILE_SHARE_READ {
            require_mutation_sharing_denied(&reopened_path)?;
        }
        let reopened_security = scenario_handle_security_digest(&reopened)?;

        let source_access_after = scenario_handle_granted_access(source)?;
        let source_identity_after = worker_handle_identity(source)?;
        let source_path_after = scenario_handle_path(source)?;
        require_scenario_namespace_binding(&source_path_after, &source_identity_after)?;
        let source_security_after = scenario_handle_security_digest(source)?;
        if source_access_before != source_access_after
            || source_identity_before != source_identity_after
            || source_identity_before != reopened_identity
            || source_path_before != source_path_after
            || source_path_before != reopened_path
            || source_security_before != source_security_after
            || source_security_before != reopened_security
        {
            return Err(AuthorityPipeError::new(WORKER_HANDLE_BINDING_MISMATCH));
        }
        Ok(reopened)
    }

    #[cfg(test)]
    pub(super) fn reopen_scenario_file_object_for_test(
        source: &File,
    ) -> Result<File, AuthorityPipeError> {
        reopen_scenario_file_object_read_only(source)
    }

    fn hash_scenario_handle(file: &File, byte_length: u64) -> Result<[u8; 32], AuthorityPipeError> {
        let source_before = worker_handle_identity(file)?;
        if source_before.size != byte_length {
            return Err(AuthorityPipeError::new(WORKER_HANDLE_SNAPSHOT_MISMATCH));
        }
        let mut reader = reopen_scenario_reader(file)?;
        let reader_before = worker_handle_identity(&reader)?;
        if reader_before != source_before {
            return Err(AuthorityPipeError::new(WORKER_HANDLE_BINDING_MISMATCH));
        }
        reader
            .seek(SeekFrom::Start(0))
            .map_err(|_| AuthorityPipeError::new(WORKER_HANDLE_SNAPSHOT_INVALID))?;
        let mut digest = Sha256::new();
        let mut offset = 0u64;
        let mut buffer = [0u8; 64 * 1024];
        while offset < byte_length {
            let take = (byte_length - offset).min(buffer.len() as u64) as usize;
            let read = reader
                .read(&mut buffer[..take])
                .map_err(|_| AuthorityPipeError::new(WORKER_HANDLE_SNAPSHOT_INVALID))?;
            if read == 0 || read > take {
                return Err(AuthorityPipeError::new(WORKER_HANDLE_SNAPSHOT_INVALID));
            }
            digest.update(&buffer[..read]);
            offset = offset
                .checked_add(read as u64)
                .ok_or_else(|| AuthorityPipeError::new(WORKER_HANDLE_SNAPSHOT_INVALID))?;
        }
        let mut trailing = [0u8; 1];
        if reader
            .read(&mut trailing)
            .map_err(|_| AuthorityPipeError::new(WORKER_HANDLE_SNAPSHOT_INVALID))?
            != 0
        {
            return Err(AuthorityPipeError::new(WORKER_HANDLE_SNAPSHOT_MISMATCH));
        }
        let reader_after = worker_handle_identity(&reader)?;
        let source_after = worker_handle_identity(file)?;
        if reader_after != reader_before || source_after != source_before {
            return Err(AuthorityPipeError::new(WORKER_HANDLE_SNAPSHOT_MISMATCH));
        }
        Ok(digest.finalize().into())
    }

    #[cfg(test)]
    fn scenario_handle_stream_position_for_test(file: &File) -> Result<u64, AuthorityPipeError> {
        let mut borrowed = file;
        borrowed
            .stream_position()
            .map_err(|_| AuthorityPipeError::new(WORKER_HANDLE_SNAPSHOT_INVALID))
    }

    #[cfg(test)]
    fn set_scenario_handle_stream_position_for_test(
        file: &File,
        position: u64,
    ) -> Result<(), AuthorityPipeError> {
        let mut borrowed = file;
        borrowed
            .seek(SeekFrom::Start(position))
            .map(|_| ())
            .map_err(|_| AuthorityPipeError::new(WORKER_HANDLE_SNAPSHOT_INVALID))
    }

    fn scenario_path_digest(path: &Path) -> [u8; 32] {
        let words: Vec<u16> = path.as_os_str().encode_wide().collect();
        let mut digest = Sha256::new();
        digest.update(WORKER_HANDLE_SNAPSHOT_DOMAIN);
        digest.update(b"path\0");
        digest.update((words.len() as u64).to_be_bytes());
        for word in words {
            digest.update(word.to_be_bytes());
        }
        digest.finalize().into()
    }

    fn fixed_scenario_snapshot_digest(
        roles: &[ScenarioHandleSnapshot; FIXED_MODEL_PART_HANDLE_COUNT],
    ) -> [u8; 32] {
        let mut digest = Sha256::new();
        digest.update(WORKER_HANDLE_SNAPSHOT_DOMAIN);
        digest.update((FIXED_MODEL_PART_HANDLE_COUNT as u64).to_be_bytes());
        for (index, (role, snapshot)) in FIXED_MODEL_PART_HANDLE_ROLES
            .iter()
            .zip(roles.iter())
            .enumerate()
        {
            digest.update((index as u64).to_be_bytes());
            digest.update((role.len() as u64).to_be_bytes());
            digest.update(role.as_bytes());
            digest.update(snapshot.identity.binding_digest());
            digest.update(snapshot.path_digest);
            digest.update(snapshot.content_digest);
            digest.update(snapshot.security_digest);
            digest.update(snapshot.granted_access.to_be_bytes());
        }
        digest.finalize().into()
    }

    impl Drop for ActiveScenarioHandleBundle {
        fn drop(&mut self) {
            drop(self.files.take());
            #[cfg(test)]
            if let Some(callback) = self.drop_callback.take() {
                callback();
            }
            self.state
                .store(SCENARIO_HANDLE_STATE_BURNED, Ordering::Release);
        }
    }

    fn scenario_handle_state(value: u8) -> ScenarioHandleBundleState {
        match value {
            SCENARIO_HANDLE_STATE_PENDING => ScenarioHandleBundleState::Pending,
            SCENARIO_HANDLE_STATE_CONSUMING => ScenarioHandleBundleState::Consuming,
            SCENARIO_HANDLE_STATE_ACTIVE => ScenarioHandleBundleState::Active,
            _ => ScenarioHandleBundleState::Burned,
        }
    }

    impl StableFileIdentity {
        fn from_information(value: &BY_HANDLE_FILE_INFORMATION) -> Self {
            Self {
                volume_serial_number: value.dwVolumeSerialNumber,
                file_index: join_u32(value.nFileIndexHigh, value.nFileIndexLow),
                size: join_u32(value.nFileSizeHigh, value.nFileSizeLow),
                creation_time: file_time_u64(value.ftCreationTime),
                last_write_time: file_time_u64(value.ftLastWriteTime),
                link_count: value.nNumberOfLinks,
            }
        }
    }

    pub(super) struct VerifiedControllerLaunchObjects {
        process_handle: Arc<OwnedHandle>,
        running_image_file: Arc<File>,
    }

    impl fmt::Debug for VerifiedControllerLaunchObjects {
        fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
            formatter
                .debug_struct("VerifiedControllerLaunchObjects")
                .finish_non_exhaustive()
        }
    }

    impl VerifiedControllerLaunchObjects {
        fn process_raw(&self) -> windows_sys::Win32::Foundation::HANDLE {
            self.process_handle.as_raw_handle().cast()
        }
    }

    pub struct AuthorityPeerIdentity {
        process_id: u32,
        session_id: u32,
        process_creation_time: u64,
        controller_path: PathBuf,
        controller_sha256: [u8; 32],
        controller_file_identity: StableFileIdentity,
        process_handle: Arc<OwnedHandle>,
        controller_file: Arc<File>,
    }

    impl fmt::Debug for AuthorityPeerIdentity {
        fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
            formatter
                .debug_struct("AuthorityPeerIdentity")
                .field("process_id", &self.process_id)
                .field("session_id", &self.session_id)
                .field("process_creation_time", &self.process_creation_time)
                .field("controller_path", &self.controller_path)
                .field("controller_sha256", &self.controller_sha256)
                .field("controller_file_identity", &self.controller_file_identity)
                .finish_non_exhaustive()
        }
    }

    impl AuthorityPeerIdentity {
        pub fn process_id(&self) -> u32 {
            self.process_id
        }

        pub fn session_id(&self) -> u32 {
            self.session_id
        }

        pub fn process_creation_time(&self) -> u64 {
            self.process_creation_time
        }

        pub fn controller_path(&self) -> &Path {
            &self.controller_path
        }

        pub fn controller_sha256(&self) -> &[u8; 32] {
            &self.controller_sha256
        }

        pub fn controller_file_identity(&self) -> StableFileIdentity {
            self.controller_file_identity
        }

        pub fn controller_file_identity_digest(&self) -> [u8; 32] {
            self.controller_file_identity.binding_digest()
        }

        pub fn revalidate(&self, policy: &AuthorityPeerPolicy) -> Result<(), AuthorityPipeError> {
            let refreshed = self.refresh()?;
            evaluate_peer_policy(
                policy,
                &AuthorityPeerFacts {
                    process_id: self.process_id,
                    process_creation_time: refreshed.process_creation_time,
                    controller_path: &refreshed.controller_path,
                    controller_sha256: refreshed.controller_sha256,
                    running_image_file_identity: refreshed.controller_file_identity,
                    protected_launcher_receipt_sha256: *policy.expected_launcher_receipt_sha256(),
                    running_process_handle_bound: true,
                    running_image_object_bound: true,
                    pipe_session_id: self.session_id,
                    token_session_id: refreshed.token_snapshot.session_id,
                    elevated: refreshed.token_snapshot.elevated,
                    high_integrity: refreshed.token_snapshot.high_integrity,
                    administrators_member: refreshed.token_snapshot.administrators_member,
                },
            )
        }

        pub fn process_handle(&self) -> BorrowedHandle<'_> {
            self.process_handle.as_handle()
        }

        pub fn controller_file_handle(&self) -> BorrowedHandle<'_> {
            self.controller_file.as_handle()
        }

        fn process_raw(&self) -> windows_sys::Win32::Foundation::HANDLE {
            self.process_handle.as_raw_handle().cast()
        }

        fn refresh(&self) -> Result<RefreshedPeerObservation, AuthorityPipeError> {
            let process = self.process_raw();
            if unsafe { GetProcessId(process) } != self.process_id {
                return Err(AuthorityPipeError::new(
                    "authority_peer_process_identity_changed",
                ));
            }
            let process_creation_time = query_process_creation_time(process)?;
            let controller_path = query_process_path(process)?;
            let token_snapshot = query_process_token(process)?;
            let (controller_sha256, controller_file_identity) =
                hash_held_running_image(self.controller_file.as_ref())?;
            let controller_path_after_hash = query_process_path(process)?;
            if process_creation_time != self.process_creation_time
                || controller_path != self.controller_path
                || controller_path_after_hash != self.controller_path
                || controller_sha256 != self.controller_sha256
                || controller_file_identity != self.controller_file_identity
                || !process_is_active(process)?
            {
                return Err(AuthorityPipeError::new(
                    "authority_peer_process_identity_changed",
                ));
            }
            Ok(RefreshedPeerObservation {
                process_creation_time,
                controller_path,
                controller_sha256,
                controller_file_identity,
                token_snapshot,
            })
        }

        fn revalidate_legacy_snapshot(
            &self,
            policy: &AuthorityPeerPolicySnapshot,
        ) -> Result<(), AuthorityPipeError> {
            let refreshed = self.refresh()?;
            evaluate_peer_snapshot(
                policy,
                &AuthorityPeerFacts {
                    process_id: self.process_id,
                    process_creation_time: refreshed.process_creation_time,
                    controller_path: &refreshed.controller_path,
                    controller_sha256: refreshed.controller_sha256,
                    running_image_file_identity: refreshed.controller_file_identity,
                    protected_launcher_receipt_sha256: policy.protected_launcher_receipt_sha256,
                    running_process_handle_bound: true,
                    running_image_object_bound: true,
                    pipe_session_id: self.session_id,
                    token_session_id: refreshed.token_snapshot.session_id,
                    elevated: refreshed.token_snapshot.elevated,
                    high_integrity: refreshed.token_snapshot.high_integrity,
                    administrators_member: refreshed.token_snapshot.administrators_member,
                },
            )
        }

        fn revalidate_installed_source(
            &self,
            source: &InstalledControllerSourcePolicy,
        ) -> Result<(), AuthorityPipeError> {
            let refreshed = self.refresh()?;
            validate_installed_controller_facts(
                source,
                &AuthorityPeerFacts {
                    process_id: self.process_id,
                    process_creation_time: refreshed.process_creation_time,
                    controller_path: &refreshed.controller_path,
                    controller_sha256: refreshed.controller_sha256,
                    running_image_file_identity: refreshed.controller_file_identity,
                    protected_launcher_receipt_sha256: [0; 32],
                    running_process_handle_bound: true,
                    running_image_object_bound: true,
                    pipe_session_id: self.session_id,
                    token_session_id: refreshed.token_snapshot.session_id,
                    elevated: refreshed.token_snapshot.elevated,
                    high_integrity: refreshed.token_snapshot.high_integrity,
                    administrators_member: refreshed.token_snapshot.administrators_member,
                },
            )
        }

        fn revalidate_installed_runtime_broker_source(
            &self,
            source: &InstalledRuntimeBrokerSourcePolicy,
        ) -> Result<(), AuthorityPipeError> {
            let refreshed = self.refresh()?;
            validate_installed_runtime_broker_facts(
                source,
                &AuthorityPeerFacts {
                    process_id: self.process_id,
                    process_creation_time: refreshed.process_creation_time,
                    controller_path: &refreshed.controller_path,
                    controller_sha256: refreshed.controller_sha256,
                    running_image_file_identity: refreshed.controller_file_identity,
                    protected_launcher_receipt_sha256: [0; 32],
                    running_process_handle_bound: true,
                    running_image_object_bound: true,
                    pipe_session_id: self.session_id,
                    token_session_id: refreshed.token_snapshot.session_id,
                    elevated: refreshed.token_snapshot.elevated,
                    high_integrity: refreshed.token_snapshot.high_integrity,
                    administrators_member: refreshed.token_snapshot.administrators_member,
                },
            )
        }
    }

    struct RefreshedPeerObservation {
        process_creation_time: u64,
        controller_path: PathBuf,
        controller_sha256: [u8; 32],
        controller_file_identity: StableFileIdentity,
        token_snapshot: TokenSnapshot,
    }

    #[derive(Debug)]
    struct AuthorityPeerPolicySnapshot {
        expected_controller_path: PathBuf,
        controller_sha256: [u8; 32],
        session_id: u32,
        process_id: u32,
        process_creation_time: u64,
        running_image_file_identity: StableFileIdentity,
        protected_launcher_receipt_sha256: [u8; 32],
    }

    impl From<&AuthorityPeerPolicy> for AuthorityPeerPolicySnapshot {
        fn from(policy: &AuthorityPeerPolicy) -> Self {
            Self {
                expected_controller_path: policy.expected_controller_path().to_path_buf(),
                controller_sha256: *policy.expected_controller_sha256(),
                session_id: policy.expected_session_id(),
                process_id: policy.expected_process_id(),
                process_creation_time: policy.expected_process_creation_time(),
                running_image_file_identity: policy.expected_running_image_file_identity(),
                protected_launcher_receipt_sha256: *policy.expected_launcher_receipt_sha256(),
            }
        }
    }

    fn evaluate_peer_snapshot(
        policy: &AuthorityPeerPolicySnapshot,
        facts: &AuthorityPeerFacts<'_>,
    ) -> Result<(), AuthorityPipeError> {
        if !facts.elevated {
            return Err(AuthorityPipeError::new("authority_peer_not_elevated"));
        }
        if !facts.high_integrity {
            return Err(AuthorityPipeError::new("authority_peer_integrity_too_low"));
        }
        if !facts.administrators_member {
            return Err(AuthorityPipeError::new("authority_peer_not_administrator"));
        }
        if facts.pipe_session_id != facts.token_session_id
            || facts.pipe_session_id != policy.session_id
        {
            return Err(AuthorityPipeError::new("authority_peer_session_mismatch"));
        }
        if facts.process_id != policy.process_id
            || facts.process_creation_time != policy.process_creation_time
        {
            return Err(AuthorityPipeError::new(
                "authority_peer_process_receipt_mismatch",
            ));
        }
        if !facts.running_process_handle_bound {
            return Err(AuthorityPipeError::new(
                "authority_peer_process_handle_unbound",
            ));
        }
        if !facts.running_image_object_bound {
            return Err(AuthorityPipeError::new(
                "authority_peer_running_image_object_unbound",
            ));
        }
        if facts.controller_path != policy.expected_controller_path {
            return Err(AuthorityPipeError::new(
                "authority_peer_controller_path_mismatch",
            ));
        }
        if facts.controller_sha256 != policy.controller_sha256 {
            return Err(AuthorityPipeError::new(
                "authority_peer_controller_digest_mismatch",
            ));
        }
        if facts.running_image_file_identity != policy.running_image_file_identity {
            return Err(AuthorityPipeError::new(
                "authority_peer_running_image_identity_mismatch",
            ));
        }
        if facts.protected_launcher_receipt_sha256 != policy.protected_launcher_receipt_sha256 {
            return Err(AuthorityPipeError::new(
                "authority_peer_launcher_receipt_mismatch",
            ));
        }
        Ok(())
    }

    #[derive(Debug)]
    enum AuthenticatedControllerPolicy {
        Legacy(AuthorityPeerPolicySnapshot),
        Installed(InstalledControllerSourcePolicy),
    }

    pub struct AuthenticatedControllerCapability {
        identity: AuthorityPeerIdentity,
        pipe_handle: Arc<OwnedHandle>,
        policy: AuthenticatedControllerPolicy,
        installed_launch: Option<InstalledControllerLaunchState>,
        scenario_handles: PendingScenarioHandleBundle,
    }

    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    pub enum RuntimeBrokerAdmissionState {
        Authenticated,
        Burned,
    }

    /// Live, one-use authority for the exact elevated helper connected to the
    /// fixed service pipe. The held process, image object, and pipe binding
    /// remain owned until this value is dropped; there is no Clone path.
    pub struct AuthenticatedRuntimeBrokerCapability {
        identity: AuthorityPeerIdentity,
        pipe_handle: Arc<OwnedHandle>,
        source: InstalledRuntimeBrokerSourcePolicy,
        broker_identity_sha256: [u8; 32],
        admission: RuntimeBrokerAdmissionLease,
    }

    struct RuntimeBrokerAdmissionLease {
        state: Arc<AtomicU8>,
    }

    impl RuntimeBrokerAdmissionLease {
        fn new() -> Self {
            Self {
                state: Arc::new(AtomicU8::new(RUNTIME_BROKER_STATE_AUTHENTICATED)),
            }
        }

        fn state(&self) -> RuntimeBrokerAdmissionState {
            runtime_broker_admission_state(self.state.load(Ordering::Acquire))
        }

        fn burn(&self) {
            self.state
                .store(RUNTIME_BROKER_STATE_BURNED, Ordering::Release);
        }

        fn revalidate_with<F>(&self, verify: F) -> Result<(), AuthorityPipeError>
        where
            F: FnOnce() -> Result<(), AuthorityPipeError>,
        {
            if self.state() != RuntimeBrokerAdmissionState::Authenticated {
                return Err(AuthorityPipeError::new(
                    "authority_runtime_broker_capability_burned",
                ));
            }
            let result = verify();
            if result.is_err() {
                self.burn();
            }
            result
        }
    }

    impl fmt::Debug for AuthenticatedRuntimeBrokerCapability {
        fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
            formatter
                .debug_struct("AuthenticatedRuntimeBrokerCapability")
                .field("identity", &self.identity)
                .field("state", &self.state())
                .finish_non_exhaustive()
        }
    }

    impl Deref for AuthenticatedRuntimeBrokerCapability {
        type Target = AuthorityPeerIdentity;

        fn deref(&self) -> &Self::Target {
            &self.identity
        }
    }

    impl AuthenticatedRuntimeBrokerCapability {
        pub fn state(&self) -> RuntimeBrokerAdmissionState {
            self.admission.state()
        }

        pub fn broker_identity_sha256(&self) -> &[u8; 32] {
            &self.broker_identity_sha256
        }

        pub(crate) fn installed_generation(&self) -> &[u8; 32] {
            &self.source.generation
        }

        pub(crate) fn service_process_id(&self) -> u32 {
            self.source.service_process_id
        }

        pub(crate) fn service_process_started_at(&self) -> u64 {
            self.source.service_process_started_at
        }

        pub(crate) fn final_commit_receipt_sha256(&self) -> &[u8; 32] {
            &self.source.final_commit_receipt_sha256
        }

        pub(crate) fn source_binding_sha256(&self) -> &[u8; 32] {
            &self.source.source_binding_sha256
        }

        pub fn revalidate_connected_peer(&self) -> Result<(), AuthorityPipeError> {
            self.admission.revalidate_with(|| {
                require_connected_runtime_broker_peer(
                    self.pipe_handle.as_raw_handle().cast(),
                    &self.identity,
                    &self.source,
                )
            })
        }
    }

    impl Drop for RuntimeBrokerAdmissionLease {
        fn drop(&mut self) {
            self.burn();
        }
    }

    fn runtime_broker_admission_state(value: u8) -> RuntimeBrokerAdmissionState {
        match value {
            RUNTIME_BROKER_STATE_AUTHENTICATED => RuntimeBrokerAdmissionState::Authenticated,
            _ => RuntimeBrokerAdmissionState::Burned,
        }
    }

    impl fmt::Debug for AuthenticatedControllerCapability {
        fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
            formatter
                .debug_struct("AuthenticatedControllerCapability")
                .field("identity", &self.identity)
                .field("controller_launch_state", &self.controller_launch_state())
                .field("scenario_handle_state", &self.scenario_handles.state())
                .finish_non_exhaustive()
        }
    }

    impl Deref for AuthenticatedControllerCapability {
        type Target = AuthorityPeerIdentity;

        fn deref(&self) -> &Self::Target {
            &self.identity
        }
    }

    impl AuthenticatedControllerCapability {
        pub fn controller_launch_state(&self) -> Option<ControllerLaunchState> {
            self.installed_launch
                .as_ref()
                .map(InstalledControllerLaunchState::state)
        }

        pub fn launch_receipt_sha256(&self) -> Option<[u8; 32]> {
            self.installed_launch
                .as_ref()
                .and_then(InstalledControllerLaunchState::receipt_sha256)
        }

        pub fn scenario_handle_state(&self) -> ScenarioHandleBundleState {
            self.scenario_handles.state()
        }

        #[cfg(test)]
        pub fn burn_pending_scenario_handles(&self) -> Result<(), AuthorityPipeError> {
            self.scenario_handles.burn()
        }

        #[cfg(test)]
        pub fn admit_external_model_part_handles(
            &self,
            tokens: ExternalModelPartHandleTokens,
        ) -> Result<ValidatedExternalScenarioHandleBundle, AuthorityPipeError> {
            admit_scenario_handles_with(
                &self.scenario_handles,
                tokens,
                || self.revalidate_connected_peer(),
                |tokens| {
                    NativeDuplicatedScenarioHandles::duplicate(self.identity.process_raw(), tokens)
                },
            )
        }

        pub fn admit_command(
            &self,
            observed: &InstalledControllerCommandIntent,
        ) -> Result<(), AuthorityPipeError> {
            let launch = self.installed_launch.as_ref().ok_or_else(|| {
                AuthorityPipeError::new("authority_installed_controller_policy_required")
            })?;
            admit_installed_command_with(launch, &self.scenario_handles, observed, || {
                self.revalidate_connected_peer()
            })
        }

        pub fn admit_external_model_part_command(
            &self,
            request_id: impl Into<String>,
            tokens: ExternalModelPartHandleTokens,
        ) -> Result<ValidatedExternalScenarioHandleBundle, AuthorityPipeError> {
            let observed =
                InstalledControllerCommandIntent::run_model_part_composition(request_id)?;
            let launch = self.installed_launch.as_ref().ok_or_else(|| {
                AuthorityPipeError::new("authority_installed_controller_policy_required")
            })?;
            admit_installed_model_part_with(
                launch,
                &self.scenario_handles,
                &observed,
                tokens,
                || self.revalidate_connected_peer(),
                |tokens| {
                    NativeDuplicatedScenarioHandles::duplicate(self.identity.process_raw(), tokens)
                },
            )
        }

        pub fn revalidate_connected_peer(&self) -> Result<(), AuthorityPipeError> {
            require_connected_pipe_peer(
                self.pipe_handle.as_raw_handle().cast(),
                &self.identity,
                &self.policy,
            )
        }
    }

    fn admit_installed_command_with<R>(
        launch: &InstalledControllerLaunchState,
        scenario_handles: &PendingScenarioHandleBundle,
        observed: &InstalledControllerCommandIntent,
        mut revalidate: R,
    ) -> Result<(), AuthorityPipeError>
    where
        R: FnMut() -> Result<(), AuthorityPipeError>,
    {
        let consuming = launch.begin_command(observed, scenario_handles)?;
        if observed.requires_model_part_handles() {
            return Err(AuthorityPipeError::new(
                "authority_controller_command_handles_required",
            ));
        }
        revalidate()?;
        scenario_handles.burn()?;
        revalidate()?;
        consuming.finish()
    }

    fn admit_installed_model_part_with<R, D>(
        launch: &InstalledControllerLaunchState,
        scenario_handles: &PendingScenarioHandleBundle,
        observed: &InstalledControllerCommandIntent,
        tokens: ExternalModelPartHandleTokens,
        revalidate: R,
        duplicate: D,
    ) -> Result<ValidatedExternalScenarioHandleBundle, AuthorityPipeError>
    where
        R: FnMut() -> Result<(), AuthorityPipeError>,
        D: FnOnce(
            ExternalModelPartHandleTokens,
        ) -> Result<NativeDuplicatedScenarioHandles, AuthorityPipeError>,
    {
        let consuming = launch.begin_command(observed, scenario_handles)?;
        if !observed.requires_model_part_handles() {
            return Err(AuthorityPipeError::new(
                "authority_controller_command_handles_unexpected",
            ));
        }
        let admitted =
            admit_scenario_handles_with(scenario_handles, tokens, revalidate, duplicate)?;
        consuming.finish()?;
        Ok(admitted)
    }

    #[derive(Debug)]
    struct NativeDuplicatedScenarioHandles {
        target_handles: [Option<OwnedHandle>; EXTERNAL_MODEL_PART_HANDLE_COUNT],
    }

    fn admit_scenario_handles_with<R, D>(
        pending: &PendingScenarioHandleBundle,
        tokens: ExternalModelPartHandleTokens,
        mut revalidate: R,
        duplicate: D,
    ) -> Result<ValidatedExternalScenarioHandleBundle, AuthorityPipeError>
    where
        R: FnMut() -> Result<(), AuthorityPipeError>,
        D: FnOnce(
            ExternalModelPartHandleTokens,
        ) -> Result<NativeDuplicatedScenarioHandles, AuthorityPipeError>,
    {
        let consuming = pending.begin(tokens)?;
        revalidate()?;
        let duplicated = duplicate(consuming.tokens())?;
        revalidate()?;
        let files = duplicated.validate_and_transfer()?;
        consuming.activate(files)
    }

    impl NativeDuplicatedScenarioHandles {
        fn duplicate(
            source_process: windows_sys::Win32::Foundation::HANDLE,
            tokens: ExternalModelPartHandleTokens,
        ) -> Result<Self, AuthorityPipeError> {
            Self::duplicate_with(tokens, |_, source| {
                duplicate_one_handle(source_process, source)
            })
        }

        fn duplicate_with<F>(
            tokens: ExternalModelPartHandleTokens,
            mut duplicate: F,
        ) -> Result<Self, AuthorityPipeError>
        where
            F: FnMut(
                usize,
                windows_sys::Win32::Foundation::HANDLE,
            ) -> Result<OwnedHandle, AuthorityPipeError>,
        {
            let mut duplicated = Self {
                target_handles: std::array::from_fn(|_| None),
            };
            for (index, value) in tokens.values().into_iter().enumerate() {
                let source = value as usize as windows_sys::Win32::Foundation::HANDLE;
                duplicated.target_handles[index] = Some(duplicate(index, source)?);
            }
            Ok(duplicated)
        }

        fn validate_and_transfer(
            mut self,
        ) -> Result<[File; EXTERNAL_MODEL_PART_HANDLE_COUNT], AuthorityPipeError> {
            if self.target_handles.iter().any(Option::is_none) {
                return Err(AuthorityPipeError::new(
                    "authority_model_part_handle_set_incomplete",
                ));
            }
            let admitted: [File; EXTERNAL_MODEL_PART_HANDLE_COUNT] = std::array::from_fn(|index| {
                File::from(
                    self.target_handles[index]
                        .take()
                        .expect("validated external handle slot"),
                )
            });
            let mut bindings = Vec::with_capacity(EXTERNAL_MODEL_PART_HANDLE_COUNT);
            let mut identities = BTreeSet::new();
            for file in &admitted {
                let raw = file.as_raw_handle().cast();
                if unsafe { GetFileType(raw) } != FILE_TYPE_DISK {
                    return Err(AuthorityPipeError::new(
                        "authority_model_part_handle_type_invalid",
                    ));
                }
                let mut flags = 0u32;
                if unsafe { GetHandleInformation(raw, &mut flags) } == 0 {
                    return Err(AuthorityPipeError::last_win32(
                        "authority_model_part_handle_flags_unavailable",
                    ));
                }
                if flags & HANDLE_FLAG_INHERIT != 0 {
                    return Err(AuthorityPipeError::new(
                        "authority_model_part_handle_inherited",
                    ));
                }
                let binding = scenario_guard_binding(file)?;
                if !identities.insert((binding.0.volume_serial_number, binding.0.file_index)) {
                    return Err(AuthorityPipeError::new(
                        "authority_model_part_handle_identity_alias",
                    ));
                }
                bindings.push(binding);
            }
            let bindings: [(StableFileIdentity, PathBuf); EXTERNAL_MODEL_PART_HANDLE_COUNT] =
                bindings
                    .try_into()
                    .map_err(|_| AuthorityPipeError::new(SCENARIO_GUARD_INVALID))?;

            let mut guards = Vec::with_capacity(EXTERNAL_MODEL_PART_HANDLE_COUNT);
            for (index, (identity, path)) in bindings.iter().enumerate() {
                let guard = reopen_service_scenario_guard(&admitted[index])?;
                if scenario_guard_binding(&guard)? != (*identity, path.clone()) {
                    return Err(AuthorityPipeError::new(SCENARIO_GUARD_BINDING_MISMATCH));
                }
                guards.push(guard);
            }
            let guards: [File; EXTERNAL_MODEL_PART_HANDLE_COUNT] = guards
                .try_into()
                .map_err(|_| AuthorityPipeError::new(SCENARIO_GUARD_INVALID))?;

            for (index, binding) in bindings.iter().enumerate() {
                if scenario_guard_binding(&admitted[index])? != *binding
                    || scenario_guard_binding(&guards[index])? != *binding
                {
                    return Err(AuthorityPipeError::new(SCENARIO_GUARD_BINDING_MISMATCH));
                }
            }
            drop(admitted);
            Ok(guards)
        }
    }

    fn scenario_guard_binding(
        file: &File,
    ) -> Result<(StableFileIdentity, PathBuf), AuthorityPipeError> {
        require_read_only_scenario_handle(file)?;
        let before = worker_handle_identity(file)
            .map_err(|_| AuthorityPipeError::new(SCENARIO_GUARD_INVALID))?;
        if before.size == 0 || before.size > MAX_SCENARIO_HANDLE_BYTES || before.link_count != 1 {
            return Err(AuthorityPipeError::new(SCENARIO_GUARD_INVALID));
        }
        let path = scenario_handle_path(file)
            .map_err(|_| AuthorityPipeError::new(SCENARIO_GUARD_INVALID))?;
        let after = worker_handle_identity(file)
            .map_err(|_| AuthorityPipeError::new(SCENARIO_GUARD_INVALID))?;
        let path_after = scenario_handle_path(file)
            .map_err(|_| AuthorityPipeError::new(SCENARIO_GUARD_INVALID))?;
        if before != after || path != path_after {
            return Err(AuthorityPipeError::new(SCENARIO_GUARD_BINDING_MISMATCH));
        }
        Ok((before, path))
    }

    fn reopen_service_scenario_guard(source: &File) -> Result<File, AuthorityPipeError> {
        let source_access = require_read_only_scenario_handle(source)
            .map_err(|_| AuthorityPipeError::new(SCENARIO_GUARD_INVALID))?;
        reopen_scenario_file_object_with_access(source, source_access, FILE_SHARE_READ).map_err(
            |error| {
                AuthorityPipeError::new(match error.code() {
                    WORKER_HANDLE_CLONE_FAILED => SCENARIO_GUARD_OPEN_FAILED,
                    WORKER_HANDLE_BINDING_MISMATCH | WORKER_HANDLE_SNAPSHOT_MISMATCH => {
                        SCENARIO_GUARD_BINDING_MISMATCH
                    }
                    _ => SCENARIO_GUARD_INVALID,
                })
            },
        )
    }

    #[cfg(test)]
    pub(super) fn service_guard_binding_for_test(
        file: &File,
    ) -> Result<(StableFileIdentity, PathBuf), AuthorityPipeError> {
        scenario_guard_binding(file)
    }

    #[cfg(test)]
    pub(super) fn service_guard_is_inheritable_for_test(
        file: &File,
    ) -> Result<bool, AuthorityPipeError> {
        let mut flags = 0u32;
        if unsafe { GetHandleInformation(file.as_raw_handle().cast(), &mut flags) } == 0 {
            return Err(AuthorityPipeError::new(SCENARIO_GUARD_INVALID));
        }
        Ok(flags & HANDLE_FLAG_INHERIT != 0)
    }

    fn duplicate_one_handle(
        source_process: windows_sys::Win32::Foundation::HANDLE,
        source: windows_sys::Win32::Foundation::HANDLE,
    ) -> Result<OwnedHandle, AuthorityPipeError> {
        let mut target = ptr::null_mut();
        if unsafe {
            DuplicateHandle(
                source_process,
                source,
                GetCurrentProcess(),
                &mut target,
                0,
                0,
                DUPLICATE_SAME_ACCESS,
            )
        } == 0
            || target.is_null()
            || target == INVALID_HANDLE_VALUE
        {
            return Err(AuthorityPipeError::last_win32(
                "authority_model_part_handle_duplicate_failed",
            ));
        }
        Ok(unsafe { OwnedHandle::from_raw_handle(target as RawHandle) })
    }

    #[cfg(test)]
    pub(super) fn duplicate_scenario_handles_with_forced_failure(
        tokens: ExternalModelPartHandleTokens,
        fail_index: usize,
    ) -> Result<(), AuthorityPipeError> {
        let duplicated =
            NativeDuplicatedScenarioHandles::duplicate_with(tokens, |index, source| {
                if index == fail_index {
                    return Err(AuthorityPipeError::new(
                        "authority_model_part_handle_duplicate_failed",
                    ));
                }
                duplicate_one_handle(unsafe { GetCurrentProcess() }, source)
            })?;
        drop(duplicated);
        Ok(())
    }

    #[cfg(test)]
    pub(super) fn duplicate_and_validate_scenario_handles_for_test(
        tokens: ExternalModelPartHandleTokens,
    ) -> Result<[File; EXTERNAL_MODEL_PART_HANDLE_COUNT], AuthorityPipeError> {
        NativeDuplicatedScenarioHandles::duplicate(unsafe { GetCurrentProcess() }, tokens)?
            .validate_and_transfer()
    }

    #[cfg(test)]
    pub(super) fn admit_current_process_handles_with_revalidation_for_test<R>(
        pending: &PendingScenarioHandleBundle,
        tokens: ExternalModelPartHandleTokens,
        revalidate: R,
    ) -> Result<ValidatedExternalScenarioHandleBundle, AuthorityPipeError>
    where
        R: FnMut() -> Result<(), AuthorityPipeError>,
    {
        admit_scenario_handles_with(pending, tokens, revalidate, |tokens| {
            NativeDuplicatedScenarioHandles::duplicate(unsafe { GetCurrentProcess() }, tokens)
        })
    }

    #[cfg(test)]
    pub(super) fn validate_installed_controller_facts_for_test(
        policy: &InstalledControllerPolicy,
        facts: &AuthorityPeerFacts<'_>,
    ) -> Result<(), AuthorityPipeError> {
        validate_installed_controller_facts(&policy.source, facts)
    }

    #[cfg(test)]
    pub(super) fn validate_installed_runtime_broker_facts_for_test(
        policy: &InstalledRuntimeBrokerPolicy,
        facts: &AuthorityPeerFacts<'_>,
    ) -> Result<(), AuthorityPipeError> {
        validate_installed_runtime_broker_facts(&policy.source, facts)
    }

    #[cfg(test)]
    pub(super) fn installed_runtime_broker_identity_for_test(
        policy: &InstalledRuntimeBrokerPolicy,
        facts: &AuthorityPeerFacts<'_>,
    ) -> Result<[u8; 32], AuthorityPipeError> {
        derive_installed_runtime_broker_identity(&policy.source, facts)
    }

    #[cfg(test)]
    pub(super) fn installed_launch_receipt_sha256_for_test(
        policy: &InstalledControllerPolicy,
        facts: &AuthorityPeerFacts<'_>,
        command_intent: &InstalledControllerCommandIntent,
    ) -> Result<([u8; 32], [u8; 32]), AuthorityPipeError> {
        derive_installed_controller_launch_seed(&policy.source, facts).map(|seed| {
            (
                seed.receipt_sha256(command_intent),
                seed.launch_identity_sha256,
            )
        })
    }

    #[cfg(test)]
    pub(super) fn claim_installed_launch_for_test(
        launch_identity_sha256: [u8; 32],
    ) -> Result<(), AuthorityPipeError> {
        claim_installed_controller_launch(&InstalledControllerLaunchSeed {
            source_binding_sha256: [0x70; 32],
            process_id: 70,
            process_creation_time: 71,
            session_id: 72,
            running_image_file_identity: StableFileIdentity {
                volume_serial_number: 73,
                file_index: 74,
                size: 75,
                creation_time: 76,
                last_write_time: 77,
                link_count: 1,
            },
            launch_identity_sha256,
        })
    }

    #[cfg(test)]
    pub(super) struct ControllerLaunchRegistryHarness {
        registry: ControllerLaunchRegistry,
    }

    #[cfg(test)]
    impl ControllerLaunchRegistryHarness {
        pub(super) fn new() -> Self {
            Self {
                registry: ControllerLaunchRegistry::default(),
            }
        }

        pub(super) fn capacity(&self) -> usize {
            MAX_CLAIMED_CONTROLLER_LAUNCHES
        }

        pub(super) fn len(&self) -> usize {
            self.registry.identities.len()
        }

        pub(super) fn claim(
            &mut self,
            launch_identity_sha256: [u8; 32],
        ) -> Result<(), AuthorityPipeError> {
            self.registry.claim(launch_identity_sha256)
        }
    }

    #[cfg(test)]
    pub(super) struct RuntimeBrokerRegistryHarness {
        registry: RuntimeBrokerRegistry,
    }

    #[cfg(test)]
    impl RuntimeBrokerRegistryHarness {
        pub(super) fn new() -> Self {
            Self {
                registry: RuntimeBrokerRegistry::default(),
            }
        }

        pub(super) fn capacity(&self) -> usize {
            MAX_CLAIMED_CONTROLLER_LAUNCHES
        }

        pub(super) fn len(&self) -> usize {
            self.registry.identities.len()
        }

        pub(super) fn claim(
            &mut self,
            broker_identity_sha256: [u8; 32],
        ) -> Result<(), AuthorityPipeError> {
            self.registry.claim(broker_identity_sha256)
        }
    }

    #[cfg(test)]
    pub(super) struct RuntimeBrokerAdmissionDropHarness {
        admission: Option<RuntimeBrokerAdmissionLease>,
        probe: Arc<AtomicU8>,
    }

    #[cfg(test)]
    impl RuntimeBrokerAdmissionDropHarness {
        pub(super) fn new() -> Self {
            let admission = RuntimeBrokerAdmissionLease::new();
            let probe = Arc::clone(&admission.state);
            Self {
                admission: Some(admission),
                probe,
            }
        }

        pub(super) fn state(&self) -> RuntimeBrokerAdmissionState {
            runtime_broker_admission_state(self.probe.load(Ordering::Acquire))
        }

        pub(super) fn drop_capability(&mut self) {
            drop(self.admission.take());
        }

        pub(super) fn fail_revalidation(&self) -> Result<(), AuthorityPipeError> {
            self.admission
                .as_ref()
                .expect("test capability must still be owned")
                .revalidate_with(|| {
                    Err(AuthorityPipeError::new(
                        "authority_runtime_broker_test_peer_drift",
                    ))
                })
        }

        pub(super) fn revalidate_after_burn(&self) -> Result<(), AuthorityPipeError> {
            self.admission
                .as_ref()
                .expect("test capability must still be owned")
                .revalidate_with(|| Ok(()))
        }
    }

    #[cfg(test)]
    pub(super) fn cancel_disconnect_result_for_test(
        cancel_error: Option<u32>,
        disconnect_error: Option<u32>,
    ) -> Result<(), AuthorityPipeError> {
        cancel_disconnect_result(cancel_error, disconnect_error)
    }

    #[cfg(test)]
    pub(super) struct InstalledControllerLaunchHarness {
        launch: InstalledControllerLaunchState,
        scenario_handles: PendingScenarioHandleBundle,
    }

    #[cfg(test)]
    impl InstalledControllerLaunchHarness {
        pub(super) fn new() -> Self {
            Self {
                launch: InstalledControllerLaunchState::new(InstalledControllerLaunchSeed {
                    source_binding_sha256: [0x71; 32],
                    process_id: 72,
                    process_creation_time: 73,
                    session_id: 74,
                    running_image_file_identity: StableFileIdentity {
                        volume_serial_number: 75,
                        file_index: 76,
                        size: 77,
                        creation_time: 78,
                        last_write_time: 79,
                        link_count: 1,
                    },
                    launch_identity_sha256: [0x72; 32],
                }),
                scenario_handles: PendingScenarioHandleBundle::default(),
            }
        }

        pub(super) fn launch_receipt_sha256(&self) -> Option<[u8; 32]> {
            self.launch.receipt_sha256()
        }

        pub(super) fn launch_state(&self) -> ControllerLaunchState {
            self.launch.state()
        }

        pub(super) fn scenario_handle_state(&self) -> ScenarioHandleBundleState {
            self.scenario_handles.state()
        }

        pub(super) fn admit_command(
            &self,
            observed: &InstalledControllerCommandIntent,
        ) -> Result<(), AuthorityPipeError> {
            admit_installed_command_with(&self.launch, &self.scenario_handles, observed, || Ok(()))
        }

        pub(super) fn admit_model_part(
            &self,
            observed: &InstalledControllerCommandIntent,
            tokens: ExternalModelPartHandleTokens,
        ) -> Result<ValidatedExternalScenarioHandleBundle, AuthorityPipeError> {
            admit_installed_model_part_with(
                &self.launch,
                &self.scenario_handles,
                observed,
                tokens,
                || Ok(()),
                |tokens| {
                    NativeDuplicatedScenarioHandles::duplicate(
                        unsafe { GetCurrentProcess() },
                        tokens,
                    )
                },
            )
        }
    }

    #[derive(Debug)]
    pub struct AuthorityPipe {
        handle: Arc<OwnedHandle>,
        io_timeout_ms: u32,
        stopping: Arc<AtomicBool>,
    }

    #[derive(Debug, Clone)]
    pub struct AuthorityPipeStopHandle {
        handle: Arc<OwnedHandle>,
        stopping: Arc<AtomicBool>,
    }

    impl AuthorityPipe {
        pub fn create() -> Result<Self, AuthorityPipeError> {
            create_pipe_with_sddl(AUTHORITY_PIPE_NAME, AUTHORITY_PIPE_SDDL)
        }

        pub fn accept_peer(
            &self,
            policy: &AuthorityPeerPolicy,
        ) -> Result<AuthenticatedControllerCapability, AuthorityPipeError> {
            self.connect_client()?;
            match authenticate_connected_peer(self.raw(), policy) {
                Ok(identity) => Ok(AuthenticatedControllerCapability {
                    identity,
                    pipe_handle: Arc::clone(&self.handle),
                    policy: AuthenticatedControllerPolicy::Legacy(policy.into()),
                    installed_launch: None,
                    scenario_handles: PendingScenarioHandleBundle::default(),
                }),
                Err(error) => {
                    unsafe {
                        DisconnectNamedPipe(self.raw());
                    }
                    Err(error)
                }
            }
        }

        pub fn accept_installed_controller(
            &self,
            policy: InstalledControllerPolicy,
        ) -> Result<AuthenticatedControllerCapability, AuthorityPipeError> {
            self.connect_client()?;
            match authenticate_installed_controller(self.raw(), &policy) {
                Ok((identity, launch_seed)) => {
                    let InstalledControllerPolicy { source } = policy;
                    Ok(AuthenticatedControllerCapability {
                        identity,
                        pipe_handle: Arc::clone(&self.handle),
                        policy: AuthenticatedControllerPolicy::Installed(source),
                        installed_launch: Some(InstalledControllerLaunchState::new(launch_seed)),
                        scenario_handles: PendingScenarioHandleBundle::default(),
                    })
                }
                Err(error) => {
                    unsafe {
                        DisconnectNamedPipe(self.raw());
                    }
                    Err(error)
                }
            }
        }

        pub fn accept_installed_runtime_broker(
            &self,
            policy: InstalledRuntimeBrokerPolicy,
        ) -> Result<AuthenticatedRuntimeBrokerCapability, AuthorityPipeError> {
            self.connect_client()?;
            match authenticate_installed_runtime_broker(self.raw(), &policy) {
                Ok((identity, broker_identity_sha256)) => {
                    let InstalledRuntimeBrokerPolicy { source } = policy;
                    Ok(AuthenticatedRuntimeBrokerCapability {
                        identity,
                        pipe_handle: Arc::clone(&self.handle),
                        source,
                        broker_identity_sha256,
                        admission: RuntimeBrokerAdmissionLease::new(),
                    })
                }
                Err(error) => {
                    unsafe {
                        DisconnectNamedPipe(self.raw());
                    }
                    Err(error)
                }
            }
        }

        pub fn handle(&self) -> BorrowedHandle<'_> {
            self.handle.as_ref().as_handle()
        }

        pub fn stop_handle(&self) -> AuthorityPipeStopHandle {
            AuthorityPipeStopHandle {
                handle: Arc::clone(&self.handle),
                stopping: Arc::clone(&self.stopping),
            }
        }

        pub fn cancel_pending_io(&self) -> Result<(), AuthorityPipeError> {
            self.stopping.store(true, Ordering::Release);
            cancel_pipe_io(self.raw())
        }

        fn raw(&self) -> windows_sys::Win32::Foundation::HANDLE {
            self.handle.as_ref().as_raw_handle().cast()
        }

        fn connect_client(&self) -> Result<(), AuthorityPipeError> {
            if self.stopping.load(Ordering::Acquire) {
                return Err(AuthorityPipeError::new("authority_pipe_stopping"));
            }
            let mut operation = PipeOverlappedOperation::new().map_err(|error| {
                AuthorityPipeError::from_io("authority_pipe_connect_event_failed", &error)
            })?;
            let connected = unsafe { ConnectNamedPipe(self.raw(), &mut operation.overlapped) };
            if connected != 0 {
                return if self.stopping.load(Ordering::Acquire) {
                    let _ = cancel_pipe_io(self.raw());
                    Err(AuthorityPipeError::new("authority_pipe_stopping"))
                } else {
                    Ok(())
                };
            }
            match unsafe { GetLastError() } {
                ERROR_PIPE_CONNECTED => {
                    if self.stopping.load(Ordering::Acquire) {
                        let _ = cancel_pipe_io(self.raw());
                        Err(AuthorityPipeError::new("authority_pipe_stopping"))
                    } else {
                        Ok(())
                    }
                }
                ERROR_IO_PENDING => {
                    if self.stopping.load(Ordering::Acquire) {
                        let _ = cancel_pipe_io(self.raw());
                    }
                    let result = wait_pipe_overlapped(
                        self.raw(),
                        &mut operation,
                        None,
                        PipeIoOperation::Connect,
                    );
                    if self.stopping.load(Ordering::Acquire) {
                        Err(AuthorityPipeError::new("authority_pipe_stopping"))
                    } else {
                        result.map(|_| ()).map_err(|error| {
                            AuthorityPipeError::from_io("authority_pipe_connect_failed", &error)
                        })
                    }
                }
                _ => Err(AuthorityPipeError::last_win32(
                    "authority_pipe_connect_failed",
                )),
            }
        }

        #[cfg(test)]
        pub(super) fn connect_client_for_test(&self) -> Result<(), AuthorityPipeError> {
            self.connect_client()
        }
    }

    impl AuthorityPipeStopHandle {
        pub fn cancel_pending_io(&self) -> Result<(), AuthorityPipeError> {
            self.stopping.store(true, Ordering::Release);
            cancel_pipe_io(self.handle.as_ref().as_raw_handle().cast())
        }
    }

    fn cancel_pipe_io(
        handle: windows_sys::Win32::Foundation::HANDLE,
    ) -> Result<(), AuthorityPipeError> {
        let cancel_error = if unsafe { CancelIoEx(handle, ptr::null()) } == 0 {
            let error = unsafe { GetLastError() };
            (error != ERROR_NOT_FOUND).then_some(error)
        } else {
            None
        };
        let disconnect_error = if unsafe { DisconnectNamedPipe(handle) } == 0 {
            let error = unsafe { GetLastError() };
            (error != ERROR_PIPE_NOT_CONNECTED).then_some(error)
        } else {
            None
        };
        cancel_disconnect_result(cancel_error, disconnect_error)
    }

    fn cancel_disconnect_result(
        cancel_error: Option<u32>,
        disconnect_error: Option<u32>,
    ) -> Result<(), AuthorityPipeError> {
        let (code, win32) = match (cancel_error, disconnect_error) {
            (None, None) => return Ok(()),
            (Some(error), None) => ("authority_pipe_cancel_failed", error),
            (None, Some(error)) => ("authority_pipe_disconnect_failed", error),
            (Some(cancel), Some(_disconnect)) => {
                ("authority_pipe_cancel_and_disconnect_failed", cancel)
            }
        };
        Err(AuthorityPipeError {
            code,
            win32: Some(win32),
        })
    }

    struct PipeOverlappedOperation {
        event: OwnedHandle,
        overlapped: OVERLAPPED,
    }

    impl PipeOverlappedOperation {
        fn new() -> io::Result<Self> {
            let event = unsafe { CreateEventW(ptr::null(), 1, 0, ptr::null()) };
            if event.is_null() {
                return Err(io::Error::last_os_error());
            }
            let event = unsafe { OwnedHandle::from_raw_handle(event as RawHandle) };
            let mut overlapped = unsafe { zeroed::<OVERLAPPED>() };
            overlapped.hEvent = event.as_raw_handle().cast();
            Ok(Self { event, overlapped })
        }
    }

    #[derive(Clone, Copy)]
    enum PipeIoOperation {
        Connect,
        Read,
        Write,
    }

    fn wait_pipe_overlapped(
        pipe: windows_sys::Win32::Foundation::HANDLE,
        operation: &mut PipeOverlappedOperation,
        timeout_ms: Option<u32>,
        operation_kind: PipeIoOperation,
    ) -> io::Result<u32> {
        let wait = unsafe {
            WaitForSingleObject(
                operation.event.as_raw_handle().cast(),
                timeout_ms.unwrap_or(u32::MAX),
            )
        };
        if wait != WAIT_OBJECT_0 {
            let wait_error = if wait == WAIT_TIMEOUT {
                io::Error::from(io::ErrorKind::TimedOut)
            } else if wait == WAIT_FAILED {
                io::Error::last_os_error()
            } else {
                io::Error::new(io::ErrorKind::Other, format!("wait={wait}"))
            };

            // OVERLAPPED storage and the caller's buffer must outlive the
            // kernel request. Always attempt both cancellation and disconnect,
            // then allow one bounded interval for terminal completion.
            let _ = cancel_pipe_io(pipe);
            if unsafe {
                WaitForSingleObject(
                    operation.event.as_raw_handle().cast(),
                    PIPE_CANCEL_SETTLE_TIMEOUT_MS,
                )
            } != WAIT_OBJECT_0
            {
                // Returning here would let the kernel retain pointers into
                // Rust stack or borrowed storage. Termination is the only
                // bounded, memory-safe fail-closed outcome.
                std::process::abort();
            }
            let mut ignored = 0u32;
            unsafe {
                GetOverlappedResult(pipe, &operation.overlapped, &mut ignored, 0);
            }
            return Err(wait_error);
        }

        let mut transferred = 0u32;
        if unsafe { GetOverlappedResult(pipe, &operation.overlapped, &mut transferred, 0) } == 0 {
            let error = unsafe { GetLastError() };
            if matches!(operation_kind, PipeIoOperation::Read)
                && error == ERROR_MORE_DATA
                && transferred != 0
            {
                return Ok(transferred);
            }
            let kind = if error == ERROR_OPERATION_ABORTED
                || error == ERROR_BROKEN_PIPE
                || error == ERROR_PIPE_NOT_CONNECTED
            {
                io::ErrorKind::ConnectionAborted
            } else {
                io::ErrorKind::Other
            };
            return Err(io::Error::new(kind, format!("win32={error}")));
        }
        Ok(transferred)
    }

    impl Read for AuthorityPipe {
        fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
            if buffer.is_empty() {
                return Ok(0);
            }
            if self.stopping.load(Ordering::Acquire) {
                return Err(io::Error::from(io::ErrorKind::ConnectionAborted));
            }
            let length = u32::try_from(buffer.len()).unwrap_or(u32::MAX);
            let mut operation = PipeOverlappedOperation::new()?;
            let mut read = 0u32;
            if unsafe {
                ReadFile(
                    self.raw(),
                    buffer.as_mut_ptr().cast(),
                    length,
                    &mut read,
                    &mut operation.overlapped,
                )
            } != 0
            {
                return if self.stopping.load(Ordering::Acquire) {
                    let _ = cancel_pipe_io(self.raw());
                    Err(io::Error::from(io::ErrorKind::ConnectionAborted))
                } else {
                    Ok(read as usize)
                };
            }
            match unsafe { GetLastError() } {
                ERROR_IO_PENDING => {
                    if self.stopping.load(Ordering::Acquire) {
                        let _ = cancel_pipe_io(self.raw());
                    }
                    wait_pipe_overlapped(
                        self.raw(),
                        &mut operation,
                        Some(self.io_timeout_ms),
                        PipeIoOperation::Read,
                    )
                    .map(|value| value as usize)
                }
                ERROR_MORE_DATA if read != 0 => Ok(read as usize),
                ERROR_BROKEN_PIPE => Ok(0),
                ERROR_OPERATION_ABORTED | ERROR_PIPE_NOT_CONNECTED => {
                    Err(io::Error::from(io::ErrorKind::ConnectionAborted))
                }
                error => Err(io::Error::new(
                    io::ErrorKind::Other,
                    format!("win32={error}"),
                )),
            }
        }
    }

    impl Write for AuthorityPipe {
        fn write(&mut self, buffer: &[u8]) -> io::Result<usize> {
            if buffer.is_empty() {
                return Ok(0);
            }
            if self.stopping.load(Ordering::Acquire) {
                return Err(io::Error::from(io::ErrorKind::ConnectionAborted));
            }
            let length = u32::try_from(buffer.len()).unwrap_or(u32::MAX);
            let mut operation = PipeOverlappedOperation::new()?;
            let mut written = 0u32;
            if unsafe {
                WriteFile(
                    self.raw(),
                    buffer.as_ptr().cast(),
                    length,
                    &mut written,
                    &mut operation.overlapped,
                )
            } != 0
            {
                return if self.stopping.load(Ordering::Acquire) {
                    let _ = cancel_pipe_io(self.raw());
                    Err(io::Error::from(io::ErrorKind::ConnectionAborted))
                } else {
                    Ok(written as usize)
                };
            }
            match unsafe { GetLastError() } {
                ERROR_IO_PENDING => {
                    if self.stopping.load(Ordering::Acquire) {
                        let _ = cancel_pipe_io(self.raw());
                    }
                    wait_pipe_overlapped(
                        self.raw(),
                        &mut operation,
                        Some(self.io_timeout_ms),
                        PipeIoOperation::Write,
                    )
                    .map(|value| value as usize)
                }
                ERROR_OPERATION_ABORTED | ERROR_BROKEN_PIPE | ERROR_PIPE_NOT_CONNECTED => {
                    Err(io::Error::from(io::ErrorKind::ConnectionAborted))
                }
                error => Err(io::Error::new(
                    io::ErrorKind::Other,
                    format!("win32={error}"),
                )),
            }
        }

        fn flush(&mut self) -> io::Result<()> {
            // A server-side kernel drain waits without a deadline for the peer
            // to consume every buffered byte. Each write above is instead an
            // independently bounded overlapped operation; protocol flush must
            // therefore remain a no-op so teardown can always disconnect.
            Ok(())
        }
    }

    impl Drop for AuthorityPipe {
        fn drop(&mut self) {
            unsafe {
                DisconnectNamedPipe(self.raw());
            }
        }
    }

    pub(super) struct SecurityDescriptor(pub(super) *mut core::ffi::c_void);

    impl SecurityDescriptor {
        pub(super) fn from_sddl(sddl: &str) -> Result<Self, AuthorityPipeError> {
            let encoded = wide_null(Path::new(sddl).as_os_str());
            let mut descriptor = ptr::null_mut();
            if unsafe {
                ConvertStringSecurityDescriptorToSecurityDescriptorW(
                    encoded.as_ptr(),
                    SDDL_REVISION_1,
                    &mut descriptor,
                    ptr::null_mut(),
                )
            } == 0
                || descriptor.is_null()
            {
                return Err(AuthorityPipeError::last_win32(
                    "authority_pipe_sddl_invalid",
                ));
            }
            Ok(Self(descriptor))
        }
    }

    impl Drop for SecurityDescriptor {
        fn drop(&mut self) {
            if !self.0.is_null() {
                unsafe {
                    LocalFree(self.0);
                }
            }
        }
    }

    #[derive(Debug, Clone, Copy)]
    struct TokenSnapshot {
        session_id: u32,
        elevated: bool,
        high_integrity: bool,
        administrators_member: bool,
    }

    struct AlignedTokenBuffer {
        words: Vec<usize>,
        byte_len: usize,
    }

    impl AlignedTokenBuffer {
        fn query(
            token: windows_sys::Win32::Foundation::HANDLE,
            class: i32,
        ) -> Result<Self, AuthorityPipeError> {
            let mut required = 0u32;
            unsafe {
                GetTokenInformation(token, class, ptr::null_mut(), 0, &mut required);
            }
            if required == 0 || unsafe { GetLastError() } != ERROR_INSUFFICIENT_BUFFER {
                return Err(AuthorityPipeError::last_win32(
                    "authority_peer_token_query_failed",
                ));
            }
            let byte_len = usize::try_from(required)
                .map_err(|_| AuthorityPipeError::new("authority_peer_token_size_invalid"))?;
            if byte_len > 64 * 1024 {
                return Err(AuthorityPipeError::new("authority_peer_token_size_invalid"));
            }
            let word_size = size_of::<usize>();
            let word_count = byte_len
                .checked_add(word_size - 1)
                .ok_or_else(|| AuthorityPipeError::new("authority_peer_token_size_invalid"))?
                / word_size;
            let mut value = Self {
                words: vec![0usize; word_count],
                byte_len,
            };
            if unsafe {
                GetTokenInformation(
                    token,
                    class,
                    value.words.as_mut_ptr().cast(),
                    required,
                    &mut required,
                )
            } == 0
            {
                return Err(AuthorityPipeError::last_win32(
                    "authority_peer_token_query_failed",
                ));
            }
            Ok(value)
        }

        fn contains(&self, pointer: *const core::ffi::c_void) -> bool {
            let start = self.words.as_ptr() as usize;
            let end = start.saturating_add(self.byte_len);
            let pointer = pointer as usize;
            pointer >= start && pointer < end
        }

        fn contains_range(&self, pointer: *const core::ffi::c_void, length: usize) -> bool {
            let start = self.words.as_ptr() as usize;
            let end = start.saturating_add(self.byte_len);
            let pointer = pointer as usize;
            pointer >= start
                && pointer
                    .checked_add(length)
                    .is_some_and(|range_end| range_end <= end)
        }
    }

    pub(super) fn create_pipe_with_sddl(
        pipe_name: &str,
        sddl: &str,
    ) -> Result<AuthorityPipe, AuthorityPipeError> {
        let security_descriptor = SecurityDescriptor::from_sddl(sddl)?;
        let mut security_attributes = SECURITY_ATTRIBUTES {
            nLength: size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: security_descriptor.0,
            bInheritHandle: 0,
        };
        let pipe_name = wide_null(Path::new(pipe_name).as_os_str());
        let handle = unsafe {
            CreateNamedPipeW(
                pipe_name.as_ptr(),
                PIPE_ACCESS_DUPLEX | FILE_FLAG_FIRST_PIPE_INSTANCE | FILE_FLAG_OVERLAPPED,
                PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
                1,
                PIPE_BUFFER_BYTES,
                PIPE_BUFFER_BYTES,
                PIPE_DEFAULT_TIMEOUT_MS,
                &mut security_attributes,
            )
        };
        if handle == INVALID_HANDLE_VALUE {
            return Err(AuthorityPipeError::last_win32(
                "authority_pipe_create_failed",
            ));
        }
        Ok(AuthorityPipe {
            handle: Arc::new(unsafe { OwnedHandle::from_raw_handle(handle as RawHandle) }),
            io_timeout_ms: PIPE_IO_TIMEOUT_MS,
            stopping: Arc::new(AtomicBool::new(false)),
        })
    }

    fn authenticate_connected_peer(
        pipe: windows_sys::Win32::Foundation::HANDLE,
        policy: &AuthorityPeerPolicy,
    ) -> Result<AuthorityPeerIdentity, AuthorityPipeError> {
        let (process_id, pipe_session_id) = query_connected_pipe_peer(pipe)?;
        if process_id != policy.expected_process_id()
            || pipe_session_id != policy.expected_session_id()
        {
            return Err(AuthorityPipeError::new(
                "authority_peer_launch_receipt_pipe_mismatch",
            ));
        }
        let launch_objects = policy.verified_launch.held_objects().ok_or_else(|| {
            AuthorityPipeError::new("authority_peer_running_image_binding_backend_disabled")
        })?;
        let process = launch_objects.process_raw();
        if unsafe { GetProcessId(process) } != process_id {
            return Err(AuthorityPipeError::new(
                "authority_peer_held_process_mismatch",
            ));
        }
        let process_creation_time = query_process_creation_time(process)?;
        let controller_path = query_process_path(process)?;
        let token_snapshot = query_process_token(process)?;
        let (controller_sha256, controller_file_identity) =
            hash_held_running_image(&launch_objects.running_image_file)?;
        let controller_path_after_hash = query_process_path(process)?;
        if controller_path_after_hash != controller_path || !process_is_active(process)? {
            return Err(AuthorityPipeError::new(
                "authority_peer_process_identity_changed",
            ));
        }
        let facts = AuthorityPeerFacts {
            process_id,
            process_creation_time,
            controller_path: &controller_path,
            controller_sha256,
            running_image_file_identity: controller_file_identity,
            protected_launcher_receipt_sha256: *policy.expected_launcher_receipt_sha256(),
            running_process_handle_bound: true,
            running_image_object_bound: true,
            pipe_session_id,
            token_session_id: token_snapshot.session_id,
            elevated: token_snapshot.elevated,
            high_integrity: token_snapshot.high_integrity,
            administrators_member: token_snapshot.administrators_member,
        };
        evaluate_peer_policy(policy, &facts)?;
        Ok(AuthorityPeerIdentity {
            process_id,
            session_id: pipe_session_id,
            process_creation_time,
            controller_path,
            controller_sha256,
            controller_file_identity,
            process_handle: Arc::clone(&launch_objects.process_handle),
            controller_file: Arc::clone(&launch_objects.running_image_file),
        })
    }

    fn authenticate_installed_controller(
        pipe: windows_sys::Win32::Foundation::HANDLE,
        policy: &InstalledControllerPolicy,
    ) -> Result<(AuthorityPeerIdentity, InstalledControllerLaunchSeed), AuthorityPipeError> {
        let (process_id, pipe_session_id) = query_connected_pipe_peer(pipe)?;
        let process_handle = open_connected_process(process_id)?;
        let process = process_handle.as_raw_handle().cast();
        if unsafe { GetProcessId(process) } != process_id {
            return Err(AuthorityPipeError::new(
                "authority_peer_held_process_mismatch",
            ));
        }
        let process_creation_time = query_process_creation_time(process)?;
        let controller_path = query_process_path(process)?;
        let controller_file = Arc::new(open_running_image(&controller_path)?);
        let (controller_sha256, controller_file_identity) =
            hash_held_running_image(controller_file.as_ref())?;
        let process_creation_time_after_hash = query_process_creation_time(process)?;
        let controller_path_after_hash = query_process_path(process)?;
        if process_creation_time_after_hash != process_creation_time
            || controller_path_after_hash != controller_path
            || !process_is_active(process)?
        {
            return Err(AuthorityPipeError::new(
                "authority_peer_process_identity_changed",
            ));
        }
        if process_id == policy.source.service_process_id {
            return Err(AuthorityPipeError::new(
                "authority_controller_process_is_service",
            ));
        }
        if process_creation_time <= policy.source.service_process_started_at {
            return Err(AuthorityPipeError::new(
                "authority_controller_predates_service",
            ));
        }
        let token_snapshot = query_process_token(process)?;
        let facts = AuthorityPeerFacts {
            process_id,
            process_creation_time,
            controller_path: &controller_path,
            controller_sha256,
            running_image_file_identity: controller_file_identity,
            protected_launcher_receipt_sha256: [0; 32],
            running_process_handle_bound: true,
            running_image_object_bound: true,
            pipe_session_id,
            token_session_id: token_snapshot.session_id,
            elevated: token_snapshot.elevated,
            high_integrity: token_snapshot.high_integrity,
            administrators_member: token_snapshot.administrators_member,
        };
        let launch_seed = derive_installed_controller_launch_seed(&policy.source, &facts)?;
        let identity = AuthorityPeerIdentity {
            process_id,
            session_id: pipe_session_id,
            process_creation_time,
            controller_path,
            controller_sha256,
            controller_file_identity,
            process_handle: Arc::new(process_handle),
            controller_file,
        };
        identity.revalidate_installed_source(&policy.source)?;
        claim_installed_controller_launch(&launch_seed)?;
        Ok((identity, launch_seed))
    }

    fn authenticate_installed_runtime_broker(
        pipe: windows_sys::Win32::Foundation::HANDLE,
        policy: &InstalledRuntimeBrokerPolicy,
    ) -> Result<(AuthorityPeerIdentity, [u8; 32]), AuthorityPipeError> {
        let (process_id, pipe_session_id) = query_connected_pipe_peer(pipe)?;
        let process_handle = open_connected_process(process_id)?;
        let process = process_handle.as_raw_handle().cast();
        if unsafe { GetProcessId(process) } != process_id {
            return Err(AuthorityPipeError::new(
                "authority_peer_held_process_mismatch",
            ));
        }
        let process_creation_time = query_process_creation_time(process)?;
        let install_helper_path = query_process_path(process)?;
        let install_helper_file = Arc::new(open_running_image(&install_helper_path)?);
        let (install_helper_sha256, install_helper_file_identity) =
            hash_held_running_image(install_helper_file.as_ref())?;
        let process_creation_time_after_hash = query_process_creation_time(process)?;
        let install_helper_path_after_hash = query_process_path(process)?;
        if process_creation_time_after_hash != process_creation_time
            || install_helper_path_after_hash != install_helper_path
            || !process_is_active(process)?
        {
            return Err(AuthorityPipeError::new(
                "authority_peer_process_identity_changed",
            ));
        }
        let token_snapshot = query_process_token(process)?;
        let facts = AuthorityPeerFacts {
            process_id,
            process_creation_time,
            controller_path: &install_helper_path,
            controller_sha256: install_helper_sha256,
            running_image_file_identity: install_helper_file_identity,
            protected_launcher_receipt_sha256: [0; 32],
            running_process_handle_bound: true,
            running_image_object_bound: true,
            pipe_session_id,
            token_session_id: token_snapshot.session_id,
            elevated: token_snapshot.elevated,
            high_integrity: token_snapshot.high_integrity,
            administrators_member: token_snapshot.administrators_member,
        };
        let broker_identity_sha256 =
            derive_installed_runtime_broker_identity(&policy.source, &facts)?;
        let identity = AuthorityPeerIdentity {
            process_id,
            session_id: pipe_session_id,
            process_creation_time,
            controller_path: install_helper_path,
            controller_sha256: install_helper_sha256,
            controller_file_identity: install_helper_file_identity,
            process_handle: Arc::new(process_handle),
            controller_file: install_helper_file,
        };
        identity.revalidate_installed_runtime_broker_source(&policy.source)?;
        claim_installed_runtime_broker(broker_identity_sha256)?;
        Ok((identity, broker_identity_sha256))
    }

    fn open_connected_process(process_id: u32) -> Result<OwnedHandle, AuthorityPipeError> {
        let process = unsafe {
            OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_DUP_HANDLE | PROCESS_SYNCHRONIZE,
                0,
                process_id,
            )
        };
        if process.is_null() || process == INVALID_HANDLE_VALUE {
            return Err(AuthorityPipeError::last_win32(
                "authority_peer_process_open_failed",
            ));
        }
        Ok(unsafe { OwnedHandle::from_raw_handle(process as RawHandle) })
    }

    fn open_running_image(path: &Path) -> Result<File, AuthorityPipeError> {
        OpenOptions::new()
            .read(true)
            .share_mode(FILE_SHARE_READ)
            .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
            .open(path)
            .map_err(|error| {
                AuthorityPipeError::from_io("authority_peer_controller_open_failed", &error)
            })
    }

    fn query_connected_pipe_peer(
        pipe: windows_sys::Win32::Foundation::HANDLE,
    ) -> Result<(u32, u32), AuthorityPipeError> {
        let mut process_id = 0u32;
        if unsafe { GetNamedPipeClientProcessId(pipe, &mut process_id) } == 0 || process_id == 0 {
            return Err(AuthorityPipeError::last_win32(
                "authority_peer_process_id_unavailable",
            ));
        }
        let mut session_id = 0u32;
        if unsafe { GetNamedPipeClientSessionId(pipe, &mut session_id) } == 0 {
            return Err(AuthorityPipeError::last_win32(
                "authority_peer_session_unavailable",
            ));
        }
        Ok((process_id, session_id))
    }

    fn require_connected_pipe_peer(
        pipe: windows_sys::Win32::Foundation::HANDLE,
        identity: &AuthorityPeerIdentity,
        policy: &AuthenticatedControllerPolicy,
    ) -> Result<(), AuthorityPipeError> {
        let (process_id, session_id) = query_connected_pipe_peer(pipe)?;
        if process_id != identity.process_id || session_id != identity.session_id {
            return Err(AuthorityPipeError::new(
                "authority_peer_connected_pipe_binding_changed",
            ));
        }
        match policy {
            AuthenticatedControllerPolicy::Legacy(policy) => {
                if process_id != policy.process_id || session_id != policy.session_id {
                    return Err(AuthorityPipeError::new(
                        "authority_peer_connected_pipe_binding_changed",
                    ));
                }
                identity.revalidate_legacy_snapshot(policy)
            }
            AuthenticatedControllerPolicy::Installed(source) => {
                identity.revalidate_installed_source(source)
            }
        }
    }

    fn require_connected_runtime_broker_peer(
        pipe: windows_sys::Win32::Foundation::HANDLE,
        identity: &AuthorityPeerIdentity,
        source: &InstalledRuntimeBrokerSourcePolicy,
    ) -> Result<(), AuthorityPipeError> {
        let (process_id, session_id) = query_connected_pipe_peer(pipe)?;
        if process_id != identity.process_id || session_id != identity.session_id {
            return Err(AuthorityPipeError::new(
                "authority_peer_connected_pipe_binding_changed",
            ));
        }
        identity.revalidate_installed_runtime_broker_source(source)
    }

    fn query_process_creation_time(
        process: windows_sys::Win32::Foundation::HANDLE,
    ) -> Result<u64, AuthorityPipeError> {
        let mut creation: FILETIME = unsafe { zeroed() };
        let mut exit: FILETIME = unsafe { zeroed() };
        let mut kernel: FILETIME = unsafe { zeroed() };
        let mut user: FILETIME = unsafe { zeroed() };
        if unsafe { GetProcessTimes(process, &mut creation, &mut exit, &mut kernel, &mut user) }
            == 0
        {
            return Err(AuthorityPipeError::last_win32(
                "authority_peer_process_times_unavailable",
            ));
        }
        let value = file_time_u64(creation);
        if value == 0 {
            return Err(AuthorityPipeError::new(
                "authority_peer_process_creation_time_invalid",
            ));
        }
        Ok(value)
    }

    fn query_process_path(
        process: windows_sys::Win32::Foundation::HANDLE,
    ) -> Result<PathBuf, AuthorityPipeError> {
        let mut buffer = vec![0u16; 32_768];
        let mut length = buffer.len() as u32;
        if unsafe { QueryFullProcessImageNameW(process, 0, buffer.as_mut_ptr(), &mut length) } == 0
            || length == 0
            || usize::try_from(length)
                .ok()
                .is_none_or(|value| value >= buffer.len())
        {
            return Err(AuthorityPipeError::last_win32(
                "authority_peer_controller_path_unavailable",
            ));
        }
        buffer.truncate(length as usize);
        Ok(PathBuf::from(String::from_utf16(&buffer).map_err(
            |_| AuthorityPipeError::new("authority_peer_controller_path_invalid"),
        )?))
    }

    pub(super) fn process_is_active(
        process: windows_sys::Win32::Foundation::HANDLE,
    ) -> Result<bool, AuthorityPipeError> {
        match unsafe { WaitForSingleObject(process, 0) } {
            WAIT_TIMEOUT => Ok(true),
            WAIT_OBJECT_0 => Ok(false),
            WAIT_FAILED => Err(AuthorityPipeError::last_win32(
                "authority_peer_process_status_unavailable",
            )),
            _ => Err(AuthorityPipeError::new(
                "authority_peer_process_status_invalid",
            )),
        }
    }

    fn query_process_token(
        process: windows_sys::Win32::Foundation::HANDLE,
    ) -> Result<TokenSnapshot, AuthorityPipeError> {
        let mut token = ptr::null_mut();
        if unsafe { OpenProcessToken(process, TOKEN_QUERY, &mut token) } == 0 || token.is_null() {
            return Err(AuthorityPipeError::last_win32(
                "authority_peer_process_token_unavailable",
            ));
        }
        let token = unsafe { OwnedHandle::from_raw_handle(token as RawHandle) };
        query_token_snapshot(token.as_raw_handle().cast())
    }

    fn query_token_snapshot(
        token: windows_sys::Win32::Foundation::HANDLE,
    ) -> Result<TokenSnapshot, AuthorityPipeError> {
        let mut elevation: TOKEN_ELEVATION = unsafe { zeroed() };
        let mut returned = 0u32;
        if unsafe {
            GetTokenInformation(
                token,
                TokenElevation,
                (&mut elevation as *mut TOKEN_ELEVATION).cast(),
                size_of::<TOKEN_ELEVATION>() as u32,
                &mut returned,
            )
        } == 0
        {
            return Err(AuthorityPipeError::last_win32(
                "authority_peer_token_elevation_unavailable",
            ));
        }
        let session_id = query_token_session_id(token)?;
        let integrity = AlignedTokenBuffer::query(token, TokenIntegrityLevel)?;
        if integrity.byte_len < size_of::<TOKEN_MANDATORY_LABEL>() {
            return Err(AuthorityPipeError::new("authority_peer_integrity_invalid"));
        }
        let label = unsafe { &*(integrity.words.as_ptr().cast::<TOKEN_MANDATORY_LABEL>()) };
        let sid = label.Label.Sid;
        if sid.is_null() || !integrity.contains(sid) || unsafe { IsValidSid(sid) } == 0 {
            return Err(AuthorityPipeError::new("authority_peer_integrity_invalid"));
        }
        let count_pointer = unsafe { GetSidSubAuthorityCount(sid) };
        if count_pointer.is_null() || !integrity.contains(count_pointer.cast()) {
            return Err(AuthorityPipeError::new("authority_peer_integrity_invalid"));
        }
        let count = unsafe { *count_pointer } as u32;
        if count == 0 {
            return Err(AuthorityPipeError::new("authority_peer_integrity_invalid"));
        }
        let rid_pointer = unsafe { GetSidSubAuthority(sid, count - 1) };
        if rid_pointer.is_null() || !integrity.contains(rid_pointer.cast()) {
            return Err(AuthorityPipeError::new("authority_peer_integrity_invalid"));
        }
        let high_integrity = unsafe { *rid_pointer } >= SECURITY_MANDATORY_HIGH_RID as u32;

        let administrators_member = token_has_enabled_administrator_group(token)?;
        Ok(TokenSnapshot {
            session_id,
            elevated: elevation.TokenIsElevated != 0,
            high_integrity,
            administrators_member,
        })
    }

    fn token_has_enabled_administrator_group(
        token: windows_sys::Win32::Foundation::HANDLE,
    ) -> Result<bool, AuthorityPipeError> {
        let mut administrator_sid = vec![0u8; SECURITY_MAX_SID_SIZE as usize];
        let mut administrator_sid_size = administrator_sid.len() as u32;
        if unsafe {
            CreateWellKnownSid(
                WinBuiltinAdministratorsSid,
                ptr::null_mut(),
                administrator_sid.as_mut_ptr().cast(),
                &mut administrator_sid_size,
            )
        } == 0
            || administrator_sid_size == 0
            || administrator_sid_size as usize > administrator_sid.len()
            || unsafe { IsValidSid(administrator_sid.as_mut_ptr().cast()) } == 0
        {
            return Err(AuthorityPipeError::last_win32(
                "authority_peer_administrator_sid_unavailable",
            ));
        }
        let administrator_sid = administrator_sid.as_mut_ptr().cast();
        let (_, enabled_member) =
            token_group_contains_sid(token, TokenGroups, administrator_sid, false, true)?;
        if !enabled_member {
            return Ok(false);
        }
        let (restricted_count, restricted_member) =
            token_group_contains_sid(token, TokenRestrictedSids, administrator_sid, true, false)?;
        Ok(restricted_count == 0 || restricted_member)
    }

    fn token_group_contains_sid(
        token: windows_sys::Win32::Foundation::HANDLE,
        class: i32,
        expected_sid: *mut core::ffi::c_void,
        allow_empty: bool,
        require_enabled: bool,
    ) -> Result<(usize, bool), AuthorityPipeError> {
        let groups_buffer = AlignedTokenBuffer::query(token, class)?;
        if groups_buffer.byte_len < size_of::<TOKEN_GROUPS>() {
            return Err(AuthorityPipeError::new(
                "authority_peer_token_groups_invalid",
            ));
        }
        let groups = unsafe { &*(groups_buffer.words.as_ptr().cast::<TOKEN_GROUPS>()) };
        let count = groups.GroupCount as usize;
        if (!allow_empty && count == 0) || count > 1_024 {
            return Err(AuthorityPipeError::new(
                "authority_peer_token_groups_invalid",
            ));
        }
        let offset = (ptr::addr_of!(groups.Groups) as usize)
            .checked_sub(groups as *const TOKEN_GROUPS as usize)
            .ok_or_else(|| AuthorityPipeError::new("authority_peer_token_groups_invalid"))?;
        let required = offset
            .checked_add(
                count
                    .checked_mul(size_of::<SID_AND_ATTRIBUTES>())
                    .ok_or_else(|| {
                        AuthorityPipeError::new("authority_peer_token_groups_invalid")
                    })?,
            )
            .ok_or_else(|| AuthorityPipeError::new("authority_peer_token_groups_invalid"))?;
        if required > groups_buffer.byte_len {
            return Err(AuthorityPipeError::new(
                "authority_peer_token_groups_invalid",
            ));
        }
        let entries = unsafe { std::slice::from_raw_parts(groups.Groups.as_ptr(), count) };
        let mut matches = 0usize;
        for entry in entries {
            if entry.Sid.is_null()
                || !groups_buffer.contains(entry.Sid)
                || unsafe { IsValidSid(entry.Sid) } == 0
            {
                return Err(AuthorityPipeError::new(
                    "authority_peer_token_groups_invalid",
                ));
            }
            let sid_length = unsafe { GetLengthSid(entry.Sid) } as usize;
            if sid_length == 0 || !groups_buffer.contains_range(entry.Sid, sid_length) {
                return Err(AuthorityPipeError::new(
                    "authority_peer_token_groups_invalid",
                ));
            }
            if unsafe { EqualSid(entry.Sid, expected_sid) } != 0
                && (!require_enabled || entry.Attributes & SE_GROUP_ENABLED as u32 != 0)
                && entry.Attributes & SE_GROUP_USE_FOR_DENY_ONLY as u32 == 0
            {
                matches += 1;
            }
        }
        if matches > 1 {
            return Err(AuthorityPipeError::new(
                "authority_peer_token_groups_invalid",
            ));
        }
        Ok((count, matches == 1))
    }

    fn query_token_session_id(
        token: windows_sys::Win32::Foundation::HANDLE,
    ) -> Result<u32, AuthorityPipeError> {
        let mut session_id = 0u32;
        let mut returned = 0u32;
        if unsafe {
            GetTokenInformation(
                token,
                TokenSessionId,
                (&mut session_id as *mut u32).cast(),
                size_of::<u32>() as u32,
                &mut returned,
            )
        } == 0
            || returned != size_of::<u32>() as u32
        {
            return Err(AuthorityPipeError::last_win32(
                "authority_peer_token_session_unavailable",
            ));
        }
        Ok(session_id)
    }

    fn hash_held_running_image(
        file: &File,
    ) -> Result<([u8; 32], StableFileIdentity), AuthorityPipeError> {
        let before = query_file_identity(file.as_raw_handle().cast())?;
        if before.size == 0 || before.size > MAX_CONTROLLER_BYTES {
            return Err(AuthorityPipeError::new(
                "authority_peer_controller_size_invalid",
            ));
        }
        let mut hasher = Sha256::new();
        let mut buffer = [0u8; 64 * 1024];
        let mut offset = 0u64;
        loop {
            let read = file.seek_read(&mut buffer, offset).map_err(|error| {
                AuthorityPipeError::from_io("authority_peer_controller_read_failed", &error)
            })?;
            if read == 0 {
                break;
            }
            hasher.update(&buffer[..read]);
            offset = offset
                .checked_add(read as u64)
                .ok_or_else(|| AuthorityPipeError::new("authority_peer_controller_size_invalid"))?;
            if offset > before.size {
                return Err(AuthorityPipeError::new(
                    "authority_peer_controller_size_invalid",
                ));
            }
        }
        let after = query_file_identity(file.as_raw_handle().cast())?;
        if before != after || offset != before.size {
            return Err(AuthorityPipeError::new(
                "authority_peer_controller_file_changed",
            ));
        }
        Ok((hasher.finalize().into(), before))
    }

    fn query_file_identity(
        handle: windows_sys::Win32::Foundation::HANDLE,
    ) -> Result<StableFileIdentity, AuthorityPipeError> {
        let mut value: BY_HANDLE_FILE_INFORMATION = unsafe { zeroed() };
        if unsafe { GetFileInformationByHandle(handle, &mut value) } == 0 {
            return Err(AuthorityPipeError::last_win32(
                "authority_peer_controller_file_identity_unavailable",
            ));
        }
        if value.dwFileAttributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT) != 0 {
            return Err(AuthorityPipeError::new(
                "authority_peer_controller_file_type_invalid",
            ));
        }
        Ok(StableFileIdentity::from_information(&value))
    }

    pub(super) fn current_process_session_id() -> Result<u32, AuthorityPipeError> {
        let mut token = ptr::null_mut();
        if unsafe { OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token) } == 0
            || token.is_null()
        {
            return Err(AuthorityPipeError::last_win32(
                "authority_self_test_token_unavailable",
            ));
        }
        let token = unsafe { OwnedHandle::from_raw_handle(token as RawHandle) };
        query_token_session_id(token.as_raw_handle().cast())
    }

    #[cfg(test)]
    pub(super) fn current_process_token_snapshot_for_test(
    ) -> Result<(u32, bool, bool, bool), AuthorityPipeError> {
        let mut token = ptr::null_mut();
        if unsafe { OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token) } == 0
            || token.is_null()
        {
            return Err(AuthorityPipeError::last_win32(
                "authority_self_test_token_unavailable",
            ));
        }
        let token = unsafe { OwnedHandle::from_raw_handle(token as RawHandle) };
        let snapshot = query_token_snapshot(token.as_raw_handle().cast())?;
        Ok((
            snapshot.session_id,
            snapshot.elevated,
            snapshot.high_integrity,
            snapshot.administrators_member,
        ))
    }

    #[cfg(test)]
    pub(super) fn current_process_installed_policy_for_test(
    ) -> Result<InstalledControllerPolicy, AuthorityPipeError> {
        let process = unsafe { GetCurrentProcess() };
        let process_id = unsafe { GetProcessId(process) };
        let process_creation_time = query_process_creation_time(process)?;
        let controller_path = query_process_path(process)?;
        let controller_file = open_running_image(&controller_path)?;
        let (controller_sha256, controller_file_identity) =
            hash_held_running_image(&controller_file)?;
        if query_process_creation_time(process)? != process_creation_time
            || query_process_path(process)? != controller_path
            || !process_is_active(process)?
        {
            return Err(AuthorityPipeError::new(
                "authority_peer_process_identity_changed",
            ));
        }
        InstalledControllerPolicy::for_test(
            [0x61; 32],
            process_id,
            process_creation_time.saturating_sub(1).max(1),
            controller_path,
            controller_sha256,
            controller_file_identity,
            [0x62; 32],
            [0x63; 32],
            [0x64; 32],
        )
    }

    pub(super) fn open_test_client(pipe_name: &str) -> Result<OwnedHandle, AuthorityPipeError> {
        let pipe_name = wide_null(Path::new(pipe_name).as_os_str());
        let handle = unsafe {
            CreateFileW(
                pipe_name.as_ptr(),
                GENERIC_READ | GENERIC_WRITE,
                0,
                ptr::null(),
                OPEN_EXISTING,
                0,
                ptr::null_mut(),
            )
        };
        if handle == INVALID_HANDLE_VALUE {
            return Err(AuthorityPipeError::last_win32(
                "authority_self_test_client_open_failed",
            ));
        }
        Ok(unsafe { OwnedHandle::from_raw_handle(handle as RawHandle) })
    }

    pub(super) fn unique_test_pipe_name() -> String {
        use std::sync::atomic::{AtomicU64, Ordering};
        static SEQUENCE: AtomicU64 = AtomicU64::new(1);
        format!(
            r"\\.\pipe\VRCForge.PrimitiveEvidence.test.{}.{}",
            std::process::id(),
            SEQUENCE.fetch_add(1, Ordering::Relaxed)
        )
    }

    fn file_time_u64(value: FILETIME) -> u64 {
        join_u32(value.dwHighDateTime, value.dwLowDateTime)
    }

    fn join_u32(high: u32, low: u32) -> u64 {
        (u64::from(high) << 32) | u64::from(low)
    }

    fn wide_null(value: &std::ffi::OsStr) -> Vec<u16> {
        value.encode_wide().chain(std::iter::once(0)).collect()
    }

    pub fn run_non_mutating_self_test() -> Result<(), AuthorityPipeError> {
        let _descriptor = SecurityDescriptor::from_sddl(AUTHORITY_PIPE_SDDL)?;
        let name = unique_test_pipe_name();
        let first = create_pipe_with_sddl(&name, TEST_PIPE_SDDL)?;
        if create_pipe_with_sddl(&name, TEST_PIPE_SDDL).is_ok() {
            return Err(AuthorityPipeError::new(
                "authority_self_test_first_instance_bypass",
            ));
        }
        drop(first);
        Ok(())
    }
}

#[cfg(all(test, windows))]
#[path = "primitive_evidence_authority_pipe/tests.rs"]
mod tests;

#[cfg(windows)]
#[allow(unused_imports)]
pub use windows::{
    run_non_mutating_self_test, ActiveScenarioHandleBundle, AuthenticatedControllerCapability,
    AuthenticatedRuntimeBrokerCapability, AuthorityPeerIdentity, AuthorityPipe,
    AuthorityPipeStopHandle, ControllerLaunchState, InstalledControllerPolicy,
    InstalledRuntimeBrokerPolicy, PendingScenarioHandleBundle, RuntimeBrokerAdmissionState,
    ScenarioHandleBundleState, ValidatedExternalScenarioHandleBundle,
};

#[cfg(windows)]
#[allow(unused_imports)]
pub(crate) use windows::{
    FixedScenarioHandleSnapshot, ScenarioStartExecutableRole,
    VerifiedScenarioExecutableCreateBinding, VerifiedScenarioExecutableLaunch,
    VerifiedScenarioStartCapability, VerifiedScenarioStartContract, WorkerScenarioHandleBundle,
};

#[cfg(not(windows))]
pub fn run_non_mutating_self_test() -> Result<(), AuthorityPipeError> {
    Err(AuthorityPipeError::new(
        "authority_pipe_platform_unsupported",
    ))
}

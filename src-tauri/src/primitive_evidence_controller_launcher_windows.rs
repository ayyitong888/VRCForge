//! Held-source controller launch foundation for the protected authority path.
//!
//! The source/handle/process evidence is complete here, while the production
//! resume/output transport remains deliberately closed. A later composition
//! step must supply both a process-bound private report channel and a
//! service-signed response transcript before this module can resume a
//! controller or expose response bytes as verified. Command-line digests are
//! expectations only; the held source and inherited kernel objects remain
//! authoritative.

#![cfg(windows)]
#![cfg_attr(not(test), allow(dead_code))]

use crate::primitive_evidence_authority_client::{
    verify_parent_controller_exchange, AuthorityClientError, AuthorityHandshakeSignatureVerifier,
    VerifiedAuthorityHandshake,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    ffi::{OsStr, OsString},
    fmt,
    fs::{File, OpenOptions},
    io,
    mem::{size_of, zeroed},
    os::windows::{
        ffi::{OsStrExt, OsStringExt},
        fs::{FileExt, OpenOptionsExt},
        io::{AsHandle, AsRawHandle, BorrowedHandle, FromRawHandle, OwnedHandle, RawHandle},
    },
    path::{Component, Path, PathBuf},
    ptr::{self, null_mut},
    sync::atomic::{compiler_fence, Ordering},
};
use windows_sys::Win32::{
    Foundation::{
        CompareObjectHandles, DuplicateHandle, GetHandleInformation, GetLastError,
        DUPLICATE_SAME_ACCESS, ERROR_INSUFFICIENT_BUFFER, ERROR_SUCCESS, FILETIME, HANDLE,
        HANDLE_FLAG_INHERIT, INVALID_HANDLE_VALUE,
    },
    Security::{GetTokenInformation, TokenSessionId, TOKEN_QUERY},
    Storage::FileSystem::{
        GetFileInformationByHandle, GetFileType, GetFinalPathNameByHandleW,
        BY_HANDLE_FILE_INFORMATION, FILE_ATTRIBUTE_DIRECTORY, FILE_ATTRIBUTE_REPARSE_POINT,
        FILE_FLAG_OPEN_REPARSE_POINT, FILE_SHARE_DELETE, FILE_SHARE_READ, FILE_TYPE_DISK,
        VOLUME_NAME_DOS,
    },
    System::{
        JobObjects::{
            CreateJobObjectW, IsProcessInJob, JobObjectExtendedLimitInformation,
            QueryInformationJobObject, SetInformationJobObject,
            JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        },
        Threading::{
            CreateProcessW, DeleteProcThreadAttributeList, GetCurrentProcess, GetProcessId,
            GetProcessTimes, InitializeProcThreadAttributeList, OpenProcessToken,
            QueryFullProcessImageNameW, UpdateProcThreadAttribute, CREATE_NO_WINDOW,
            CREATE_SUSPENDED, EXTENDED_STARTUPINFO_PRESENT, LPPROC_THREAD_ATTRIBUTE_LIST,
            PROCESS_INFORMATION, PROC_THREAD_ATTRIBUTE_HANDLE_LIST, PROC_THREAD_ATTRIBUTE_JOB_LIST,
            STARTUPINFOEXW,
        },
    },
};

const EXTERNAL_HANDLE_COUNT: usize = 6;
const CONTROLLER_EXCHANGE_SCHEMA: &str = "vrcforge.primitive_evidence_controller_exchange.v1";
const CONTROLLER_RESPONSE_TRANSCRIPT_SCHEMA: &str =
    "vrcforge.primitive_evidence_controller_response_transcript.v1";
const CONTROLLER_CANONICAL_RESPONSE_DOMAIN: &[u8] = b"vrcforge-controller-canonical-response-v1\0";
const CONTROLLER_RESPONSE_TRANSCRIPT_DOMAIN: &[u8] =
    b"vrcforge-controller-response-transcript-v1\0";
const CONTROLLER_PEER_BINDING_DOMAIN: &[u8] = b"vrcforge-authority-peer-binding-v1\0";
const CONTROLLER_FILE_IDENTITY_DOMAIN: &[u8] = b"vrcforge-authority-file-identity-v1\0";
const CONTROLLER_RUN_LAUNCH_BINDING_DOMAIN: &[u8] = b"vrcforge-controller-run-launch-binding-v1\0";
const CONTROLLER_ADMISSION_EXPECTATION_SCHEMA: &str =
    "vrcforge.primitive_evidence_controller_launch_expectation.v1";
const CONTROLLER_ADMISSION_EXPECTATION_DOMAIN: &[u8] =
    b"vrcforge-controller-launch-expectation-v1\0";
const INSTALLED_CONTROLLER_LAUNCH_IDENTITY_DOMAIN: &[u8] =
    b"vrcforge-authority-installed-controller-launch-identity-v1\0";
const PRODUCTION_SIGNED_EXCHANGE_BLOCKER: &str =
    "authority_controller_signed_response_transport_not_connected";
const MAX_CONTROLLER_IMAGE_BYTES: u64 = 64 * 1024 * 1024;
const MAX_CONTROLLER_EXCHANGE_BYTES: usize = 32 * 1024 * 1024;
const MAX_CONTROLLER_ADMISSION_EXPECTATION_BYTES: usize = 4 * 1024;
const MAX_CONTROLLER_RESPONSE_TRANSCRIPT_BYTES: usize = 8 * 1024;
const ATTRIBUTE_LIST_MAX_BYTES: usize = 1024 * 1024;

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ControllerLauncherError {
    code: &'static str,
    win32: Option<u32>,
}

impl ControllerLauncherError {
    fn new(code: &'static str) -> Self {
        Self { code, win32: None }
    }

    fn last_win32(code: &'static str) -> Self {
        Self {
            code,
            win32: Some(unsafe { windows_sys::Win32::Foundation::GetLastError() }),
        }
    }

    fn from_io(code: &'static str, error: &io::Error) -> Self {
        Self {
            code,
            win32: error
                .raw_os_error()
                .and_then(|value| u32::try_from(value).ok()),
        }
    }

    pub(crate) fn code(&self) -> &'static str {
        self.code
    }

    pub(crate) fn win32(&self) -> Option<u32> {
        self.win32
    }
}

impl fmt::Display for ControllerLauncherError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self.win32 {
            Some(win32) => write!(formatter, "{} (win32={win32})", self.code),
            None => formatter.write_str(self.code),
        }
    }
}

impl std::error::Error for ControllerLauncherError {}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct ControllerFileIdentity {
    volume_serial: u32,
    file_id: u64,
    byte_length: u64,
    creation_time: u64,
    last_write_time: u64,
    link_count: u32,
}

impl ControllerFileIdentity {
    fn from_information(
        value: &BY_HANDLE_FILE_INFORMATION,
    ) -> Result<Self, ControllerLauncherError> {
        if value.dwFileAttributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT) != 0
            || value.dwVolumeSerialNumber == 0
            || value.nNumberOfLinks != 1
        {
            return Err(ControllerLauncherError::new(
                "authority_controller_source_identity_invalid",
            ));
        }
        let file_id = join_u32(value.nFileIndexHigh, value.nFileIndexLow);
        let byte_length = join_u32(value.nFileSizeHigh, value.nFileSizeLow);
        let creation_time = file_time_u64(value.ftCreationTime);
        let last_write_time = file_time_u64(value.ftLastWriteTime);
        if file_id == 0
            || byte_length == 0
            || byte_length > MAX_CONTROLLER_IMAGE_BYTES
            || creation_time == 0
            || last_write_time == 0
        {
            return Err(ControllerLauncherError::new(
                "authority_controller_source_identity_invalid",
            ));
        }
        Ok(Self {
            volume_serial: value.dwVolumeSerialNumber,
            file_id,
            byte_length,
            creation_time,
            last_write_time,
            link_count: value.nNumberOfLinks,
        })
    }

    fn binding_digest(&self) -> [u8; 32] {
        let mut digest = Sha256::new();
        digest.update(CONTROLLER_FILE_IDENTITY_DOMAIN);
        digest.update(self.volume_serial.to_be_bytes());
        digest.update(self.file_id.to_be_bytes());
        digest.update(self.byte_length.to_be_bytes());
        digest.update(self.creation_time.to_be_bytes());
        digest.update(self.last_write_time.to_be_bytes());
        digest.update(self.link_count.to_be_bytes());
        digest.finalize().into()
    }
}

/// Held, exact source authority for one controller launch. This value is
/// deliberately non-Clone and retains the authenticated file object until the
/// child process has been created and revalidated.
pub(crate) struct AuthenticatedControllerLaunchSource {
    controller_file: File,
    controller_path: PathBuf,
    controller_final_path: PathBuf,
    controller_sha256: [u8; 32],
    controller_identity: ControllerFileIdentity,
    generation_sha256: [u8; 32],
    signer_key_id: [u8; 32],
    service_process_id: u32,
    service_process_started_at: u64,
    source_binding_sha256: [u8; 32],
}

impl fmt::Debug for AuthenticatedControllerLaunchSource {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("AuthenticatedControllerLaunchSource")
            .field("controller_path", &self.controller_path)
            .field("generation_sha256", &"<redacted>")
            .field("signer_key_id", &"<redacted>")
            .field("source_binding_sha256", &"<redacted>")
            .finish_non_exhaustive()
    }
}

impl AuthenticatedControllerLaunchSource {
    #[allow(clippy::too_many_arguments)]
    fn from_held_source(
        controller_file: File,
        controller_path: PathBuf,
        expected_controller_sha256: [u8; 32],
        expected_controller_byte_length: u64,
        expected_volume_serial: u32,
        expected_file_id: u64,
        expected_link_count: u32,
        generation_sha256: [u8; 32],
        signer_key_id: [u8; 32],
        service_process_id: u32,
        service_process_started_at: u64,
        source_binding_sha256: [u8; 32],
        test_path_override: bool,
    ) -> Result<Self, ControllerLauncherError> {
        validate_absolute_controller_path(&controller_path)?;
        if [
            &expected_controller_sha256,
            &generation_sha256,
            &signer_key_id,
            &source_binding_sha256,
        ]
        .into_iter()
        .any(|digest| is_zero_digest(digest))
            || expected_controller_byte_length == 0
            || expected_controller_byte_length > MAX_CONTROLLER_IMAGE_BYTES
            || expected_volume_serial == 0
            || expected_file_id == 0
            || expected_link_count != 1
            || service_process_id == 0
            || service_process_started_at == 0
        {
            return Err(ControllerLauncherError::new(
                "authority_controller_launch_source_invalid",
            ));
        }
        if !test_path_override {
            validate_installed_controller_path(&controller_path, &generation_sha256)?;
        }
        let (controller_sha256, controller_identity) = hash_held_controller(&controller_file)?;
        if controller_sha256 != expected_controller_sha256
            || controller_identity.byte_length != expected_controller_byte_length
            || controller_identity.volume_serial != expected_volume_serial
            || controller_identity.file_id != expected_file_id
            || controller_identity.link_count != expected_link_count
        {
            return Err(ControllerLauncherError::new(
                "authority_controller_launch_source_mismatch",
            ));
        }
        let controller_final_path = final_path_for_handle(&controller_file)?;
        let mut source = Self {
            controller_file,
            controller_path,
            controller_final_path,
            controller_sha256,
            controller_identity,
            generation_sha256,
            signer_key_id,
            service_process_id,
            service_process_started_at,
            source_binding_sha256,
        };
        source.verify_still_stable()?;
        Ok(source)
    }

    fn verify_still_stable(&mut self) -> Result<(), ControllerLauncherError> {
        let (digest, identity) = hash_held_controller(&self.controller_file)?;
        if digest != self.controller_sha256
            || identity != self.controller_identity
            || final_path_for_handle(&self.controller_file)? != self.controller_final_path
        {
            return Err(ControllerLauncherError::new(
                "authority_controller_launch_source_changed",
            ));
        }
        Ok(())
    }

    #[cfg(test)]
    fn for_current_test_executable() -> Result<Self, ControllerLauncherError> {
        let controller_path = std::env::current_exe().map_err(|error| {
            ControllerLauncherError::from_io("authority_controller_test_source_missing", &error)
        })?;
        let controller_file = open_controller_file(&controller_path)?;
        let (sha256, identity) = hash_held_controller(&controller_file)?;
        Self::from_held_source(
            controller_file,
            controller_path,
            sha256,
            identity.byte_length,
            identity.volume_serial,
            identity.file_id,
            identity.link_count,
            [0x71; 32],
            [0x72; 32],
            u32::MAX,
            1,
            [0x73; 32],
            true,
        )
    }
}

pub(crate) struct ControllerRunLaunchRequest {
    request_id: String,
    transaction_sha256: [u8; 32],
    expected_external_binding_sha256: [u8; 32],
}

impl fmt::Debug for ControllerRunLaunchRequest {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ControllerRunLaunchRequest")
            .field("request_id", &self.request_id)
            .field("transaction_sha256", &"<redacted>")
            .field("expected_external_binding_sha256", &"<redacted>")
            .finish()
    }
}

impl ControllerRunLaunchRequest {
    pub(crate) fn new(
        request_id: String,
        transaction_sha256: [u8; 32],
        expected_external_binding_sha256: [u8; 32],
    ) -> Result<Self, ControllerLauncherError> {
        if !valid_request_id(&request_id)
            || is_zero_digest(&transaction_sha256)
            || is_zero_digest(&expected_external_binding_sha256)
        {
            return Err(ControllerLauncherError::new(
                "authority_controller_launch_request_invalid",
            ));
        }
        Ok(Self {
            request_id,
            transaction_sha256,
            expected_external_binding_sha256,
        })
    }
}

struct InheritableExternalSix {
    handles: [OwnedHandle; EXTERNAL_HANDLE_COUNT],
}

impl InheritableExternalSix {
    fn duplicate_from(
        sources: [BorrowedHandle<'_>; EXTERNAL_HANDLE_COUNT],
    ) -> Result<Self, ControllerLauncherError> {
        let current = unsafe { GetCurrentProcess() };
        let mut duplicated = Vec::with_capacity(EXTERNAL_HANDLE_COUNT);
        for (index, source) in sources.into_iter().enumerate() {
            let source_raw = source.as_raw_handle() as HANDLE;
            if source_raw.is_null()
                || source_raw == INVALID_HANDLE_VALUE
                || unsafe { GetFileType(source_raw) } != FILE_TYPE_DISK
            {
                return Err(ControllerLauncherError::new(
                    "authority_controller_external_handle_invalid",
                ));
            }
            for prior in &sources[..index] {
                if unsafe { CompareObjectHandles(source_raw, prior.as_raw_handle() as HANDLE) } != 0
                {
                    return Err(ControllerLauncherError::new(
                        "authority_controller_external_handle_alias",
                    ));
                }
            }
            let mut duplicate = null_mut();
            if unsafe {
                DuplicateHandle(
                    current,
                    source_raw,
                    current,
                    &mut duplicate,
                    0,
                    1,
                    DUPLICATE_SAME_ACCESS,
                )
            } == 0
                || duplicate.is_null()
                || duplicate == INVALID_HANDLE_VALUE
            {
                return Err(ControllerLauncherError::last_win32(
                    "authority_controller_external_handle_duplicate_failed",
                ));
            }
            let duplicate = unsafe { OwnedHandle::from_raw_handle(duplicate as RawHandle) };
            let mut flags = 0u32;
            if unsafe { GetHandleInformation(duplicate.as_raw_handle() as HANDLE, &mut flags) } == 0
                || flags != HANDLE_FLAG_INHERIT
                || unsafe { CompareObjectHandles(source_raw, duplicate.as_raw_handle() as HANDLE) }
                    == 0
            {
                return Err(ControllerLauncherError::new(
                    "authority_controller_external_handle_duplicate_invalid",
                ));
            }
            duplicated.push(duplicate);
        }
        let handles = duplicated.try_into().map_err(|_| {
            ControllerLauncherError::new("authority_controller_external_handle_count_invalid")
        })?;
        Ok(Self { handles })
    }

    fn raw_handles(&self) -> [HANDLE; EXTERNAL_HANDLE_COUNT] {
        self.handles
            .each_ref()
            .map(|handle| handle.as_raw_handle() as HANDLE)
    }

    fn wire_values(&self) -> [String; EXTERNAL_HANDLE_COUNT] {
        self.raw_handles()
            .map(|handle| format!("{:016x}", handle as usize as u64))
    }
}

/// Sole parent-owned handle for an unnamed job whose only limit is
/// kill-on-close. The handle is non-inheritable and is never duplicated or
/// placed in the controller handle list, so dropping this owner is the
/// process-containment primitive even when direct terminate/wait APIs are
/// unavailable or return an uncertain result.
struct KillOnCloseControllerJob {
    handle: Option<OwnedHandle>,
}

impl KillOnCloseControllerJob {
    fn new() -> Result<Self, ControllerLauncherError> {
        let raw = unsafe { CreateJobObjectW(ptr::null(), ptr::null()) };
        if raw.is_null() || raw == INVALID_HANDLE_VALUE {
            return Err(ControllerLauncherError::last_win32(
                "authority_controller_containment_job_create_failed",
            ));
        }
        let handle = unsafe { OwnedHandle::from_raw_handle(raw as RawHandle) };
        let mut flags = u32::MAX;
        if unsafe { GetHandleInformation(raw, &mut flags) } == 0 || flags != 0 {
            return Err(ControllerLauncherError::new(
                "authority_controller_containment_job_handle_invalid",
            ));
        }

        let mut limits = unsafe { zeroed::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() };
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if unsafe {
            SetInformationJobObject(
                raw,
                JobObjectExtendedLimitInformation,
                (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
                size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            )
        } == 0
        {
            return Err(ControllerLauncherError::last_win32(
                "authority_controller_containment_job_configure_failed",
            ));
        }

        let mut readback = unsafe { zeroed::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() };
        let mut returned = 0u32;
        if unsafe {
            QueryInformationJobObject(
                raw,
                JobObjectExtendedLimitInformation,
                (&mut readback as *mut JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
                size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                &mut returned,
            )
        } == 0
            || returned != size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32
            || readback.BasicLimitInformation.LimitFlags != JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        {
            return Err(ControllerLauncherError::new(
                "authority_controller_containment_job_readback_invalid",
            ));
        }
        Ok(Self {
            handle: Some(handle),
        })
    }

    fn raw(&self) -> HANDLE {
        self.handle
            .as_ref()
            .expect("controller containment job must remain owned")
            .as_raw_handle() as HANDLE
    }

    fn verify_process_member(
        &self,
        process: BorrowedHandle<'_>,
    ) -> Result<(), ControllerLauncherError> {
        let mut member = 0;
        if unsafe { IsProcessInJob(process.as_raw_handle() as HANDLE, self.raw(), &mut member) }
            == 0
        {
            return Err(ControllerLauncherError::last_win32(
                "authority_controller_containment_job_membership_invalid",
            ));
        }
        if member == 0 {
            return Err(ControllerLauncherError::new(
                "authority_controller_containment_job_membership_invalid",
            ));
        }
        Ok(())
    }

    fn close_for_containment(&mut self) {
        drop(self.handle.take());
        compiler_fence(Ordering::SeqCst);
    }
}

impl Drop for KillOnCloseControllerJob {
    fn drop(&mut self) {
        self.close_for_containment();
    }
}

struct ExactHandleAttributeList {
    storage: Vec<usize>,
    inherited: Box<[HANDLE; EXTERNAL_HANDLE_COUNT]>,
    containment_job: Box<[HANDLE; 1]>,
    initialized: bool,
}

impl ExactHandleAttributeList {
    fn new(
        handles: [HANDLE; EXTERNAL_HANDLE_COUNT],
        containment_job: HANDLE,
    ) -> Result<Self, ControllerLauncherError> {
        if handles
            .iter()
            .any(|handle| handle.is_null() || *handle == INVALID_HANDLE_VALUE)
            || containment_job.is_null()
            || containment_job == INVALID_HANDLE_VALUE
        {
            return Err(ControllerLauncherError::new(
                "authority_controller_handle_list_invalid",
            ));
        }
        let mut required = 0usize;
        unsafe {
            windows_sys::Win32::Foundation::SetLastError(ERROR_SUCCESS);
        }
        if unsafe { InitializeProcThreadAttributeList(null_mut(), 2, 0, &mut required) } != 0
            || unsafe { GetLastError() } != ERROR_INSUFFICIENT_BUFFER
            || required == 0
            || required > ATTRIBUTE_LIST_MAX_BYTES
        {
            return Err(ControllerLauncherError::new(
                "authority_controller_attribute_list_size_invalid",
            ));
        }
        let word_count = required
            .checked_add(size_of::<usize>() - 1)
            .ok_or_else(|| {
                ControllerLauncherError::new("authority_controller_attribute_list_size_invalid")
            })?
            / size_of::<usize>();
        let mut value = Self {
            storage: vec![0usize; word_count],
            inherited: Box::new(handles),
            containment_job: Box::new([containment_job]),
            initialized: false,
        };
        if unsafe { InitializeProcThreadAttributeList(value.raw(), 2, 0, &mut required) } == 0 {
            return Err(ControllerLauncherError::last_win32(
                "authority_controller_attribute_list_init_failed",
            ));
        }
        value.initialized = true;
        if unsafe {
            UpdateProcThreadAttribute(
                value.raw(),
                0,
                PROC_THREAD_ATTRIBUTE_HANDLE_LIST as usize,
                value.inherited.as_ptr().cast(),
                size_of::<[HANDLE; EXTERNAL_HANDLE_COUNT]>(),
                null_mut(),
                ptr::null(),
            )
        } == 0
        {
            return Err(ControllerLauncherError::last_win32(
                "authority_controller_handle_list_attribute_failed",
            ));
        }
        if unsafe {
            UpdateProcThreadAttribute(
                value.raw(),
                0,
                PROC_THREAD_ATTRIBUTE_JOB_LIST as usize,
                value.containment_job.as_ptr().cast(),
                size_of::<[HANDLE; 1]>(),
                null_mut(),
                ptr::null(),
            )
        } == 0
        {
            return Err(ControllerLauncherError::last_win32(
                "authority_controller_job_list_attribute_failed",
            ));
        }
        Ok(value)
    }

    fn raw(&mut self) -> LPPROC_THREAD_ATTRIBUTE_LIST {
        self.storage.as_mut_ptr().cast()
    }
}

impl Drop for ExactHandleAttributeList {
    fn drop(&mut self) {
        if self.initialized {
            unsafe {
                DeleteProcThreadAttributeList(self.storage.as_mut_ptr().cast());
            }
            self.initialized = false;
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct ControllerProcessEvidence {
    process_id: u32,
    creation_time: u64,
    session_id: u32,
    controller_sha256: [u8; 32],
    controller_file_identity_sha256: [u8; 32],
    peer_binding_sha256: [u8; 32],
}

impl ControllerProcessEvidence {
    fn capture(
        process: BorrowedHandle<'_>,
        source: &mut AuthenticatedControllerLaunchSource,
    ) -> Result<Self, ControllerLauncherError> {
        source.verify_still_stable()?;
        let raw = process.as_raw_handle() as HANDLE;
        let process_id = unsafe { GetProcessId(raw) };
        let creation_time = process_creation_time(raw)?;
        let session_id = process_session_id(raw)?;
        if process_id == 0
            || session_id == 0
            || process_id == source.service_process_id
            || creation_time <= source.service_process_started_at
        {
            return Err(ControllerLauncherError::new(
                "authority_controller_process_identity_invalid",
            ));
        }
        let process_path = process_image_path(raw)?;
        let process_image = open_controller_file(&process_path)?;
        let process_final_path = final_path_for_handle(&process_image)?;
        let (controller_sha256, controller_identity) = hash_held_controller(&process_image)?;
        if process_final_path != source.controller_final_path
            || controller_sha256 != source.controller_sha256
            || controller_identity != source.controller_identity
            || process_image_path(raw)? != process_path
        {
            return Err(ControllerLauncherError::new(
                "authority_controller_running_image_mismatch",
            ));
        }
        source.verify_still_stable()?;
        let controller_file_identity_sha256 = controller_identity.binding_digest();
        let peer_binding_sha256 = controller_peer_binding(
            process_id,
            creation_time,
            session_id,
            &controller_sha256,
            &controller_file_identity_sha256,
        );
        Ok(Self {
            process_id,
            creation_time,
            session_id,
            controller_sha256,
            controller_file_identity_sha256,
            peer_binding_sha256,
        })
    }
}

pub(crate) struct PendingControllerLaunch {
    containment_job: KillOnCloseControllerJob,
    process: OwnedHandle,
    primary_thread: OwnedHandle,
    source: AuthenticatedControllerLaunchSource,
    request: ControllerRunLaunchRequest,
    evidence: ControllerProcessEvidence,
    inherited_wire_values: [String; EXTERNAL_HANDLE_COUNT],
    launch_binding_sha256: [u8; 32],
}

/// Transport-safe expectation projected by the parent that still owns the
/// suspended process. It contains no raw handles and is never authorization by
/// itself. The service must consume it once through a private admission
/// channel, then cross-check every field against the live connected pipe peer
/// and its authenticated FinalCommit source before minting a production pipe
/// admission.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ControllerLaunchAdmissionExpectation {
    request_id: String,
    generation_sha256: [u8; 32],
    transaction_sha256: [u8; 32],
    expected_external_binding_sha256: [u8; 32],
    source_binding_sha256: [u8; 32],
    installed_controller_launch_identity_sha256: [u8; 32],
    launch_binding_sha256: [u8; 32],
    process_id: u32,
    process_creation_time: u64,
    session_id: u32,
    controller_sha256: [u8; 32],
    controller_file_identity_sha256: [u8; 32],
    peer_binding_sha256: [u8; 32],
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct ControllerLaunchAdmissionExpectationWire {
    canonical_byte_length: u32,
    controller_file_identity_sha256: String,
    controller_sha256: String,
    expected_external_binding_sha256: String,
    generation_sha256: String,
    installed_controller_launch_identity_sha256: String,
    launch_binding_sha256: String,
    peer_binding_sha256: String,
    process_creation_time: u64,
    process_id: u32,
    request_id: String,
    schema: String,
    session_id: u32,
    source_binding_sha256: String,
    transaction_sha256: String,
}

impl ControllerLaunchAdmissionExpectation {
    pub(crate) fn canonical_bytes(&self) -> Result<Vec<u8>, ControllerLauncherError> {
        let mut wire = ControllerLaunchAdmissionExpectationWire {
            canonical_byte_length: 0,
            controller_file_identity_sha256: hex_lower(&self.controller_file_identity_sha256),
            controller_sha256: hex_lower(&self.controller_sha256),
            expected_external_binding_sha256: hex_lower(&self.expected_external_binding_sha256),
            generation_sha256: hex_lower(&self.generation_sha256),
            installed_controller_launch_identity_sha256: hex_lower(
                &self.installed_controller_launch_identity_sha256,
            ),
            launch_binding_sha256: hex_lower(&self.launch_binding_sha256),
            peer_binding_sha256: hex_lower(&self.peer_binding_sha256),
            process_creation_time: self.process_creation_time,
            process_id: self.process_id,
            request_id: self.request_id.clone(),
            schema: CONTROLLER_ADMISSION_EXPECTATION_SCHEMA.to_owned(),
            session_id: self.session_id,
            source_binding_sha256: hex_lower(&self.source_binding_sha256),
            transaction_sha256: hex_lower(&self.transaction_sha256),
        };
        for _ in 0..3 {
            let encoded = serde_json::to_vec(&wire).map_err(|_| {
                ControllerLauncherError::new(
                    "authority_controller_launch_expectation_encode_failed",
                )
            })?;
            let byte_length = u32::try_from(encoded.len()).map_err(|_| {
                ControllerLauncherError::new(
                    "authority_controller_launch_expectation_length_invalid",
                )
            })?;
            if byte_length as usize > MAX_CONTROLLER_ADMISSION_EXPECTATION_BYTES {
                return Err(ControllerLauncherError::new(
                    "authority_controller_launch_expectation_length_invalid",
                ));
            }
            if wire.canonical_byte_length == byte_length {
                return Ok(encoded);
            }
            wire.canonical_byte_length = byte_length;
        }
        Err(ControllerLauncherError::new(
            "authority_controller_launch_expectation_length_invalid",
        ))
    }

    pub(crate) fn decode_canonical(bytes: &[u8]) -> Result<Self, ControllerLauncherError> {
        if bytes.is_empty() || bytes.len() > MAX_CONTROLLER_ADMISSION_EXPECTATION_BYTES {
            return Err(ControllerLauncherError::new(
                "authority_controller_launch_expectation_length_invalid",
            ));
        }
        let wire: ControllerLaunchAdmissionExpectationWire = serde_json::from_slice(bytes)
            .map_err(|_| {
                ControllerLauncherError::new(
                    "authority_controller_launch_expectation_decode_failed",
                )
            })?;
        let reencoded = serde_json::to_vec(&wire).map_err(|_| {
            ControllerLauncherError::new("authority_controller_launch_expectation_decode_failed")
        })?;
        if reencoded != bytes
            || wire.canonical_byte_length as usize != bytes.len()
            || wire.schema != CONTROLLER_ADMISSION_EXPECTATION_SCHEMA
            || !valid_request_id(&wire.request_id)
            || wire.process_id == 0
            || wire.process_creation_time == 0
            || wire.session_id == 0
        {
            return Err(ControllerLauncherError::new(
                "authority_controller_launch_expectation_shape_invalid",
            ));
        }
        let value = Self {
            request_id: wire.request_id,
            generation_sha256: parse_lower_hex_digest(&wire.generation_sha256)?,
            transaction_sha256: parse_lower_hex_digest(&wire.transaction_sha256)?,
            expected_external_binding_sha256: parse_lower_hex_digest(
                &wire.expected_external_binding_sha256,
            )?,
            source_binding_sha256: parse_lower_hex_digest(&wire.source_binding_sha256)?,
            installed_controller_launch_identity_sha256: parse_lower_hex_digest(
                &wire.installed_controller_launch_identity_sha256,
            )?,
            launch_binding_sha256: parse_lower_hex_digest(&wire.launch_binding_sha256)?,
            process_id: wire.process_id,
            process_creation_time: wire.process_creation_time,
            session_id: wire.session_id,
            controller_sha256: parse_lower_hex_digest(&wire.controller_sha256)?,
            controller_file_identity_sha256: parse_lower_hex_digest(
                &wire.controller_file_identity_sha256,
            )?,
            peer_binding_sha256: parse_lower_hex_digest(&wire.peer_binding_sha256)?,
        };
        value.validate_bindings()?;
        if value.canonical_bytes()?.as_slice() != bytes {
            return Err(ControllerLauncherError::new(
                "authority_controller_launch_expectation_shape_invalid",
            ));
        }
        Ok(value)
    }

    fn validate_bindings(&self) -> Result<(), ControllerLauncherError> {
        if [
            &self.generation_sha256,
            &self.transaction_sha256,
            &self.expected_external_binding_sha256,
            &self.source_binding_sha256,
            &self.installed_controller_launch_identity_sha256,
            &self.launch_binding_sha256,
            &self.controller_sha256,
            &self.controller_file_identity_sha256,
            &self.peer_binding_sha256,
        ]
        .into_iter()
        .any(|digest| is_zero_digest(digest))
            || self.generation_sha256 == self.transaction_sha256
            || self.peer_binding_sha256
                != controller_peer_binding(
                    self.process_id,
                    self.process_creation_time,
                    self.session_id,
                    &self.controller_sha256,
                    &self.controller_file_identity_sha256,
                )
            || self.installed_controller_launch_identity_sha256
                != installed_controller_launch_identity(
                    &self.source_binding_sha256,
                    self.process_id,
                    self.process_creation_time,
                    self.session_id,
                    &self.controller_file_identity_sha256,
                )
            || self.launch_binding_sha256
                != controller_run_launch_binding(
                    &self.request_id,
                    &self.source_binding_sha256,
                    &self.installed_controller_launch_identity_sha256,
                    &self.generation_sha256,
                    &self.transaction_sha256,
                    &self.expected_external_binding_sha256,
                    &self.peer_binding_sha256,
                )
        {
            return Err(ControllerLauncherError::new(
                "authority_controller_launch_expectation_binding_invalid",
            ));
        }
        Ok(())
    }

    pub(crate) fn request_id(&self) -> &str {
        &self.request_id
    }

    pub(crate) fn generation_sha256(&self) -> &[u8; 32] {
        &self.generation_sha256
    }

    pub(crate) fn transaction_sha256(&self) -> &[u8; 32] {
        &self.transaction_sha256
    }

    pub(crate) fn expected_external_binding_sha256(&self) -> &[u8; 32] {
        &self.expected_external_binding_sha256
    }

    pub(crate) fn source_binding_sha256(&self) -> &[u8; 32] {
        &self.source_binding_sha256
    }

    pub(crate) fn installed_controller_launch_identity_sha256(&self) -> &[u8; 32] {
        &self.installed_controller_launch_identity_sha256
    }

    pub(crate) fn launch_binding_sha256(&self) -> &[u8; 32] {
        &self.launch_binding_sha256
    }

    pub(crate) fn process_id(&self) -> u32 {
        self.process_id
    }

    pub(crate) fn process_creation_time(&self) -> u64 {
        self.process_creation_time
    }

    pub(crate) fn session_id(&self) -> u32 {
        self.session_id
    }

    pub(crate) fn controller_sha256(&self) -> &[u8; 32] {
        &self.controller_sha256
    }

    pub(crate) fn controller_file_identity_sha256(&self) -> &[u8; 32] {
        &self.controller_file_identity_sha256
    }

    pub(crate) fn peer_binding_sha256(&self) -> &[u8; 32] {
        &self.peer_binding_sha256
    }

    pub(crate) fn digest(&self) -> Result<[u8; 32], ControllerLauncherError> {
        let canonical = self.canonical_bytes()?;
        let mut digest = Sha256::new();
        digest.update(CONTROLLER_ADMISSION_EXPECTATION_DOMAIN);
        digest.update((canonical.len() as u64).to_be_bytes());
        digest.update(canonical);
        Ok(digest.finalize().into())
    }
}

impl fmt::Debug for PendingControllerLaunch {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("PendingControllerLaunch")
            .field("process_id", &self.evidence.process_id)
            .field("request_id", &self.request.request_id)
            .field("inherited_roles", &EXTERNAL_HANDLE_COUNT)
            .field("launch_binding_sha256", &"<redacted>")
            .finish_non_exhaustive()
    }
}

impl PendingControllerLaunch {
    /// Projects the sole cross-process expectation while this parent retains
    /// the suspended process/thread and held source. The projection cannot be
    /// used to recreate this affine owner.
    pub(crate) fn admission_expectation(&self) -> ControllerLaunchAdmissionExpectation {
        ControllerLaunchAdmissionExpectation {
            request_id: self.request.request_id.clone(),
            generation_sha256: self.source.generation_sha256,
            transaction_sha256: self.request.transaction_sha256,
            expected_external_binding_sha256: self.request.expected_external_binding_sha256,
            source_binding_sha256: self.source.source_binding_sha256,
            installed_controller_launch_identity_sha256: installed_controller_launch_identity(
                &self.source.source_binding_sha256,
                self.evidence.process_id,
                self.evidence.creation_time,
                self.evidence.session_id,
                &self.evidence.controller_file_identity_sha256,
            ),
            launch_binding_sha256: self.launch_binding_sha256,
            process_id: self.evidence.process_id,
            process_creation_time: self.evidence.creation_time,
            session_id: self.evidence.session_id,
            controller_sha256: self.evidence.controller_sha256,
            controller_file_identity_sha256: self.evidence.controller_file_identity_sha256,
            peer_binding_sha256: self.evidence.peer_binding_sha256,
        }
    }

    /// The process cannot be resumed until a private, process-bound output
    /// transport and a service-signed response transcript are composed.
    /// Calling this burns the pending launch through `Drop`; it never silently
    /// falls back to stdout, environment, a file, or an unsigned response.
    pub(crate) fn resume_and_exchange(
        self,
    ) -> Result<VerifiedControllerExchange, ControllerLauncherError> {
        Err(ControllerLauncherError::new(
            PRODUCTION_SIGNED_EXCHANGE_BLOCKER,
        ))
    }

    #[cfg(test)]
    fn process_raw_for_test(&self) -> HANDLE {
        self.process.as_raw_handle() as HANDLE
    }

    #[cfg(test)]
    fn containment_job_raw_for_test(&self) -> HANDLE {
        self.containment_job.raw()
    }

    #[cfg(test)]
    fn inherited_wire_values_for_test(&self) -> &[String; EXTERNAL_HANDLE_COUNT] {
        &self.inherited_wire_values
    }
}

impl Drop for PendingControllerLaunch {
    fn drop(&mut self) {
        // This is the sole non-inheritable handle to an unnamed
        // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE job. Closing it contains the
        // controller even if direct process termination or waiting would be
        // unavailable or uncertain.
        self.containment_job.close_for_containment();
        compiler_fence(Ordering::SeqCst);
    }
}

pub(crate) fn begin_authenticated_controller_run_launch(
    mut source: AuthenticatedControllerLaunchSource,
    request: ControllerRunLaunchRequest,
    external_handles: [BorrowedHandle<'_>; EXTERNAL_HANDLE_COUNT],
) -> Result<PendingControllerLaunch, ControllerLauncherError> {
    source.verify_still_stable()?;
    if source.generation_sha256 == request.transaction_sha256 {
        return Err(ControllerLauncherError::new(
            "authority_controller_launch_context_invalid",
        ));
    }
    let inherited = InheritableExternalSix::duplicate_from(external_handles)?;
    let inherited_wire_values = inherited.wire_values();
    let containment_job = KillOnCloseControllerJob::new()?;
    let mut attributes =
        ExactHandleAttributeList::new(inherited.raw_handles(), containment_job.raw())?;
    let command_line = controller_command_line(&source, &request, &inherited_wire_values)?;
    let application = wide_null(source.controller_path.as_os_str());
    let current_directory = source.controller_path.parent().ok_or_else(|| {
        ControllerLauncherError::new("authority_controller_launch_source_invalid")
    })?;
    let current_directory = wide_null(current_directory.as_os_str());
    let mut command_line = command_line;
    let mut startup = unsafe { zeroed::<STARTUPINFOEXW>() };
    startup.StartupInfo.cb = size_of::<STARTUPINFOEXW>() as u32;
    startup.lpAttributeList = attributes.raw();
    let mut process_information = unsafe { zeroed::<PROCESS_INFORMATION>() };
    let create_succeeded = unsafe {
        CreateProcessW(
            application.as_ptr(),
            command_line.as_mut_ptr(),
            ptr::null(),
            ptr::null(),
            1,
            CREATE_SUSPENDED | EXTENDED_STARTUPINFO_PRESENT | CREATE_NO_WINDOW,
            ptr::null(),
            current_directory.as_ptr(),
            &startup.StartupInfo,
            &mut process_information,
        )
    };
    let process = (!process_information.hProcess.is_null()
        && process_information.hProcess != INVALID_HANDLE_VALUE)
        .then(|| unsafe {
            OwnedHandle::from_raw_handle(process_information.hProcess as RawHandle)
        });
    let primary_thread = (!process_information.hThread.is_null()
        && process_information.hThread != INVALID_HANDLE_VALUE)
        .then(|| unsafe { OwnedHandle::from_raw_handle(process_information.hThread as RawHandle) });
    if create_succeeded == 0 {
        return Err(ControllerLauncherError::last_win32(
            "authority_controller_process_create_failed",
        ));
    }
    let (process, primary_thread) = match (process, primary_thread) {
        (Some(process), Some(primary_thread))
            if process_information.dwProcessId != 0 && process_information.dwThreadId != 0 =>
        {
            (process, primary_thread)
        }
        _ => {
            return Err(ControllerLauncherError::new(
                "authority_controller_process_information_invalid",
            ))
        }
    };
    drop(attributes);
    drop(inherited);
    containment_job.verify_process_member(process.as_handle())?;
    let evidence = ControllerProcessEvidence::capture(process.as_handle(), &mut source)?;
    if evidence.process_id != process_information.dwProcessId {
        return Err(ControllerLauncherError::new(
            "authority_controller_process_identity_mismatch",
        ));
    }
    let installed_controller_launch_identity_sha256 = installed_controller_launch_identity(
        &source.source_binding_sha256,
        evidence.process_id,
        evidence.creation_time,
        evidence.session_id,
        &evidence.controller_file_identity_sha256,
    );
    let launch_binding_sha256 = controller_run_launch_binding(
        &request.request_id,
        &source.source_binding_sha256,
        &installed_controller_launch_identity_sha256,
        &source.generation_sha256,
        &request.transaction_sha256,
        &request.expected_external_binding_sha256,
        &evidence.peer_binding_sha256,
    );
    Ok(PendingControllerLaunch {
        containment_job,
        process,
        primary_thread,
        source,
        request,
        evidence,
        inherited_wire_values,
        launch_binding_sha256,
    })
}

#[derive(Debug, Clone)]
struct CompletedControllerProcess {
    expectation: ControllerLaunchAdmissionExpectation,
    signer_key_id: [u8; 32],
    command: &'static str,
    exit_code: u32,
    output: Vec<u8>,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct ControllerExchangeEnvelope {
    schema: String,
    command: String,
    requires_upper_layer_verification: bool,
    handshake_raw_json: String,
    response_raw_json: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ControllerResponseTranscriptWire<'a> {
    canonical_handshake_sha256: String,
    canonical_response_sha256: String,
    command: &'a str,
    controller_file_identity_sha256: String,
    controller_sha256: String,
    expected_external_binding_sha256: String,
    generation_sha256: String,
    handshake_attestation_sha256: String,
    handshake_signature_p256: String,
    installed_controller_launch_identity_sha256: String,
    launch_binding_sha256: String,
    peer_binding_sha256: String,
    process_creation_time: u64,
    process_id: u32,
    request_id: &'a str,
    schema: &'static str,
    session_id: u32,
    signer_key_id: String,
    source_binding_sha256: String,
    transaction_sha256: String,
}

struct UnsignedControllerResponseTranscript {
    canonical_bytes: Vec<u8>,
    digest_sha256: [u8; 32],
}

impl fmt::Debug for UnsignedControllerResponseTranscript {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("UnsignedControllerResponseTranscript")
            .field("canonical_byte_len", &self.canonical_bytes.len())
            .field("digest_sha256", &"<unverified>")
            .finish_non_exhaustive()
    }
}

pub(crate) struct UnverifiedControllerExchange {
    transcript: UnsignedControllerResponseTranscript,
    handshake: VerifiedAuthorityHandshake,
    response_bytes: Vec<u8>,
}

impl fmt::Debug for UnverifiedControllerExchange {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("UnverifiedControllerExchange")
            .field(
                "transcript_canonical_byte_len",
                &self.transcript.canonical_bytes.len(),
            )
            .field("response_byte_len", &self.response_bytes.len())
            .finish_non_exhaustive()
    }
}

/// Reserved success type for a future service-signed response transcript.
/// There is intentionally no constructor or conversion from
/// [`UnverifiedControllerExchange`] while the service response schema carries
/// no transcript signature.
#[allow(dead_code)]
pub(crate) enum VerifiedControllerExchange {}

impl fmt::Debug for VerifiedControllerExchange {
    fn fmt(&self, _formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match *self {}
    }
}

fn parse_unverified_completed_controller_exchange<V>(
    completed: CompletedControllerProcess,
    verifier: &mut V,
) -> Result<UnverifiedControllerExchange, ControllerLauncherError>
where
    V: AuthorityHandshakeSignatureVerifier,
{
    if completed.exit_code != 0
        || completed.output.is_empty()
        || completed.output.len() > MAX_CONTROLLER_EXCHANGE_BYTES
        || completed.command != "runModelPartComposition"
        || is_zero_digest(&completed.signer_key_id)
    {
        return Err(ControllerLauncherError::new(
            "authority_controller_process_completion_invalid",
        ));
    }
    completed.expectation.validate_bindings()?;
    let payload = completed.output.strip_suffix(b"\n").ok_or_else(|| {
        ControllerLauncherError::new("authority_controller_output_termination_invalid")
    })?;
    if payload.is_empty() || payload.contains(&b'\n') || payload.contains(&b'\r') {
        return Err(ControllerLauncherError::new(
            "authority_controller_output_termination_invalid",
        ));
    }
    let envelope: ControllerExchangeEnvelope = serde_json::from_slice(payload)
        .map_err(|_| ControllerLauncherError::new("authority_controller_output_json_invalid"))?;
    let canonical = serde_json::to_vec(&envelope)
        .map_err(|_| ControllerLauncherError::new("authority_controller_output_json_invalid"))?;
    if canonical != payload
        || envelope.schema != CONTROLLER_EXCHANGE_SCHEMA
        || envelope.command != completed.command
        || !envelope.requires_upper_layer_verification
    {
        return Err(ControllerLauncherError::new(
            "authority_controller_output_shape_invalid",
        ));
    }
    let handshake = verify_parent_controller_exchange(
        envelope.handshake_raw_json.as_bytes(),
        envelope.response_raw_json.as_bytes(),
        completed.expectation.generation_sha256(),
        completed.expectation.peer_binding_sha256(),
        &completed.signer_key_id,
        completed.command,
        verifier,
    )
    .map_err(map_client_error)?;
    let response_bytes = envelope.response_raw_json.into_bytes();
    let transcript = unsigned_controller_response_transcript(
        &completed.expectation,
        completed.command,
        &completed.signer_key_id,
        &handshake,
        &response_bytes,
    )?;
    Ok(UnverifiedControllerExchange {
        transcript,
        handshake,
        response_bytes,
    })
}

fn unsigned_controller_response_transcript(
    expectation: &ControllerLaunchAdmissionExpectation,
    command: &str,
    signer_key_id: &[u8; 32],
    handshake: &VerifiedAuthorityHandshake,
    canonical_response: &[u8],
) -> Result<UnsignedControllerResponseTranscript, ControllerLauncherError> {
    let response_value: serde_json::Value =
        serde_json::from_slice(canonical_response).map_err(|_| {
            ControllerLauncherError::new("authority_controller_response_transcript_input_invalid")
        })?;
    let reencoded_response = serde_json::to_vec(&response_value).map_err(|_| {
        ControllerLauncherError::new("authority_controller_response_transcript_input_invalid")
    })?;
    if command != "runModelPartComposition"
        || signer_key_id != handshake.signer_key_id()
        || expectation.generation_sha256() != handshake.generation_sha256()
        || expectation.peer_binding_sha256() != handshake.peer_binding_sha256()
        || canonical_response.is_empty()
        || canonical_response.len() > MAX_CONTROLLER_EXCHANGE_BYTES
        || reencoded_response != canonical_response
        || response_value
            .get("command")
            .and_then(serde_json::Value::as_str)
            != Some(command)
    {
        return Err(ControllerLauncherError::new(
            "authority_controller_response_transcript_input_invalid",
        ));
    }
    expectation.validate_bindings()?;
    let mut response_digest = Sha256::new();
    response_digest.update(CONTROLLER_CANONICAL_RESPONSE_DOMAIN);
    response_digest.update((canonical_response.len() as u64).to_be_bytes());
    response_digest.update(canonical_response);
    let canonical_response_sha256: [u8; 32] = response_digest.finalize().into();
    let canonical_bytes = serde_json::to_vec(&ControllerResponseTranscriptWire {
        canonical_handshake_sha256: hex_lower(handshake.canonical_handshake_sha256()),
        canonical_response_sha256: hex_lower(&canonical_response_sha256),
        command,
        controller_file_identity_sha256: hex_lower(expectation.controller_file_identity_sha256()),
        controller_sha256: hex_lower(expectation.controller_sha256()),
        expected_external_binding_sha256: hex_lower(expectation.expected_external_binding_sha256()),
        generation_sha256: hex_lower(expectation.generation_sha256()),
        handshake_attestation_sha256: hex_lower(handshake.attestation_digest()),
        handshake_signature_p256: hex_lower(handshake.signature_p256()),
        installed_controller_launch_identity_sha256: hex_lower(
            expectation.installed_controller_launch_identity_sha256(),
        ),
        launch_binding_sha256: hex_lower(expectation.launch_binding_sha256()),
        peer_binding_sha256: hex_lower(expectation.peer_binding_sha256()),
        process_creation_time: expectation.process_creation_time(),
        process_id: expectation.process_id(),
        request_id: expectation.request_id(),
        schema: CONTROLLER_RESPONSE_TRANSCRIPT_SCHEMA,
        session_id: expectation.session_id(),
        signer_key_id: hex_lower(signer_key_id),
        source_binding_sha256: hex_lower(expectation.source_binding_sha256()),
        transaction_sha256: hex_lower(expectation.transaction_sha256()),
    })
    .map_err(|_| {
        ControllerLauncherError::new("authority_controller_response_transcript_encode_failed")
    })?;
    if canonical_bytes.is_empty()
        || canonical_bytes.len() > MAX_CONTROLLER_RESPONSE_TRANSCRIPT_BYTES
    {
        return Err(ControllerLauncherError::new(
            "authority_controller_response_transcript_length_invalid",
        ));
    }
    let mut transcript_digest = Sha256::new();
    transcript_digest.update(CONTROLLER_RESPONSE_TRANSCRIPT_DOMAIN);
    transcript_digest.update((canonical_bytes.len() as u64).to_be_bytes());
    transcript_digest.update(&canonical_bytes);
    Ok(UnsignedControllerResponseTranscript {
        canonical_bytes,
        digest_sha256: transcript_digest.finalize().into(),
    })
}

fn map_client_error(error: AuthorityClientError) -> ControllerLauncherError {
    ControllerLauncherError {
        code: error.code(),
        win32: error.win32(),
    }
}

fn controller_command_line(
    source: &AuthenticatedControllerLaunchSource,
    request: &ControllerRunLaunchRequest,
    inherited: &[String; EXTERNAL_HANDLE_COUNT],
) -> Result<Vec<u16>, ControllerLauncherError> {
    let mut arguments = Vec::with_capacity(11);
    arguments.push(source.controller_path.as_os_str().to_owned());
    arguments.push(OsString::from("--run-model-part-composition"));
    arguments.push(OsString::from(&request.request_id));
    arguments.push(OsString::from(hex_lower(&source.generation_sha256)));
    arguments.push(OsString::from(hex_lower(&request.transaction_sha256)));
    arguments.push(OsString::from(hex_lower(
        &request.expected_external_binding_sha256,
    )));
    arguments.extend(inherited.iter().map(OsString::from));
    let command = arguments
        .iter()
        .map(|argument| quote_windows_argument(argument))
        .collect::<Vec<_>>()
        .join(" ");
    if command.encode_utf16().count() >= 32_767 {
        return Err(ControllerLauncherError::new(
            "authority_controller_command_line_too_long",
        ));
    }
    Ok(command.encode_utf16().chain(std::iter::once(0)).collect())
}

fn quote_windows_argument(value: &OsStr) -> String {
    let value = value.to_string_lossy();
    if !value.is_empty()
        && !value
            .chars()
            .any(|character| character.is_ascii_whitespace() || character == '"')
    {
        return value.into_owned();
    }
    let mut output = String::from("\"");
    let mut backslashes = 0usize;
    for character in value.chars() {
        match character {
            '\\' => backslashes += 1,
            '"' => {
                output.push_str(&"\\".repeat(backslashes * 2 + 1));
                output.push('"');
                backslashes = 0;
            }
            _ => {
                output.push_str(&"\\".repeat(backslashes));
                backslashes = 0;
                output.push(character);
            }
        }
    }
    output.push_str(&"\\".repeat(backslashes * 2));
    output.push('"');
    output
}

fn validate_absolute_controller_path(path: &Path) -> Result<(), ControllerLauncherError> {
    if !path.is_absolute()
        || path.as_os_str().is_empty()
        || path
            .components()
            .any(|component| matches!(component, Component::CurDir | Component::ParentDir))
    {
        return Err(ControllerLauncherError::new(
            "authority_controller_launch_path_invalid",
        ));
    }
    Ok(())
}

fn validate_installed_controller_path(
    path: &Path,
    generation_sha256: &[u8; 32],
) -> Result<(), ControllerLauncherError> {
    if path.file_name().and_then(|value| value.to_str())
        != Some("vrcforge_primitive_evidence_controller.exe")
        || path
            .parent()
            .and_then(Path::file_name)
            .and_then(|value| value.to_str())
            != Some(hex_lower(generation_sha256).as_str())
    {
        return Err(ControllerLauncherError::new(
            "authority_controller_launch_path_invalid",
        ));
    }
    Ok(())
}

fn valid_request_id(value: &str) -> bool {
    let mut characters = value.chars();
    characters
        .next()
        .is_some_and(|first| first.is_ascii_alphanumeric())
        && value.len() <= 128
        && characters.all(|character| {
            character.is_ascii_alphanumeric() || matches!(character, '-' | '_' | '.' | ':')
        })
}

fn open_controller_file(path: &Path) -> Result<File, ControllerLauncherError> {
    OpenOptions::new()
        .read(true)
        .share_mode(FILE_SHARE_READ | FILE_SHARE_DELETE)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
        .open(path)
        .map_err(|error| {
            ControllerLauncherError::from_io("authority_controller_source_open_failed", &error)
        })
}

fn hash_held_controller(
    file: &File,
) -> Result<([u8; 32], ControllerFileIdentity), ControllerLauncherError> {
    let before = query_file_identity(file)?;
    let mut digest = Sha256::new();
    let mut buffer = [0u8; 64 * 1024];
    let mut offset = 0u64;
    loop {
        let read = file.seek_read(&mut buffer, offset).map_err(|error| {
            ControllerLauncherError::from_io("authority_controller_source_read_failed", &error)
        })?;
        if read == 0 {
            break;
        }
        digest.update(&buffer[..read]);
        offset = offset.checked_add(read as u64).ok_or_else(|| {
            ControllerLauncherError::new("authority_controller_source_size_invalid")
        })?;
        if offset > before.byte_length {
            return Err(ControllerLauncherError::new(
                "authority_controller_source_size_invalid",
            ));
        }
    }
    let after = query_file_identity(file)?;
    if before != after || offset != before.byte_length {
        return Err(ControllerLauncherError::new(
            "authority_controller_source_changed",
        ));
    }
    Ok((digest.finalize().into(), before))
}

fn query_file_identity(file: &File) -> Result<ControllerFileIdentity, ControllerLauncherError> {
    let mut information = unsafe { zeroed::<BY_HANDLE_FILE_INFORMATION>() };
    if unsafe { GetFileInformationByHandle(file.as_raw_handle() as HANDLE, &mut information) } == 0
    {
        return Err(ControllerLauncherError::last_win32(
            "authority_controller_source_identity_unavailable",
        ));
    }
    ControllerFileIdentity::from_information(&information)
}

fn final_path_for_handle(file: &File) -> Result<PathBuf, ControllerLauncherError> {
    let raw = file.as_raw_handle() as HANDLE;
    let required = unsafe { GetFinalPathNameByHandleW(raw, null_mut(), 0, VOLUME_NAME_DOS) };
    if required == 0 || required > 32_768 {
        return Err(ControllerLauncherError::last_win32(
            "authority_controller_source_final_path_unavailable",
        ));
    }
    let mut buffer = vec![0u16; required as usize + 1];
    let written = unsafe {
        GetFinalPathNameByHandleW(
            raw,
            buffer.as_mut_ptr(),
            buffer.len() as u32,
            VOLUME_NAME_DOS,
        )
    };
    if written == 0 || written >= buffer.len() as u32 {
        return Err(ControllerLauncherError::last_win32(
            "authority_controller_source_final_path_unavailable",
        ));
    }
    buffer.truncate(written as usize);
    Ok(PathBuf::from(OsString::from_wide(&buffer)))
}

fn process_creation_time(process: HANDLE) -> Result<u64, ControllerLauncherError> {
    let mut creation = unsafe { zeroed::<FILETIME>() };
    let mut exit = unsafe { zeroed::<FILETIME>() };
    let mut kernel = unsafe { zeroed::<FILETIME>() };
    let mut user = unsafe { zeroed::<FILETIME>() };
    if unsafe { GetProcessTimes(process, &mut creation, &mut exit, &mut kernel, &mut user) } == 0 {
        return Err(ControllerLauncherError::last_win32(
            "authority_controller_process_times_unavailable",
        ));
    }
    let value = file_time_u64(creation);
    if value == 0 {
        return Err(ControllerLauncherError::new(
            "authority_controller_process_times_invalid",
        ));
    }
    Ok(value)
}

fn process_session_id(process: HANDLE) -> Result<u32, ControllerLauncherError> {
    let mut token = null_mut();
    if unsafe { OpenProcessToken(process, TOKEN_QUERY, &mut token) } == 0 || token.is_null() {
        return Err(ControllerLauncherError::last_win32(
            "authority_controller_process_token_unavailable",
        ));
    }
    let token = unsafe { OwnedHandle::from_raw_handle(token as RawHandle) };
    let mut session_id = 0u32;
    let mut returned = 0u32;
    if unsafe {
        GetTokenInformation(
            token.as_raw_handle() as HANDLE,
            TokenSessionId,
            (&mut session_id as *mut u32).cast(),
            size_of::<u32>() as u32,
            &mut returned,
        )
    } == 0
        || returned != size_of::<u32>() as u32
        || session_id == 0
    {
        return Err(ControllerLauncherError::last_win32(
            "authority_controller_process_session_unavailable",
        ));
    }
    Ok(session_id)
}

fn process_image_path(process: HANDLE) -> Result<PathBuf, ControllerLauncherError> {
    let mut buffer = vec![0u16; 32_768];
    let mut length = buffer.len() as u32;
    if unsafe { QueryFullProcessImageNameW(process, 0, buffer.as_mut_ptr(), &mut length) } == 0
        || length == 0
        || length as usize >= buffer.len()
    {
        return Err(ControllerLauncherError::last_win32(
            "authority_controller_process_path_unavailable",
        ));
    }
    buffer.truncate(length as usize);
    Ok(PathBuf::from(OsString::from_wide(&buffer)))
}

fn file_time_u64(value: FILETIME) -> u64 {
    join_u32(value.dwHighDateTime, value.dwLowDateTime)
}

fn join_u32(high: u32, low: u32) -> u64 {
    (u64::from(high) << 32) | u64::from(low)
}

fn controller_peer_binding(
    process_id: u32,
    process_creation_time: u64,
    session_id: u32,
    controller_sha256: &[u8; 32],
    controller_file_identity_sha256: &[u8; 32],
) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(CONTROLLER_PEER_BINDING_DOMAIN);
    digest.update(process_id.to_be_bytes());
    digest.update(process_creation_time.to_be_bytes());
    digest.update(session_id.to_be_bytes());
    digest.update(controller_sha256);
    digest.update(controller_file_identity_sha256);
    digest.finalize().into()
}

fn installed_controller_launch_identity(
    source_binding_sha256: &[u8; 32],
    process_id: u32,
    process_creation_time: u64,
    session_id: u32,
    controller_file_identity_sha256: &[u8; 32],
) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(INSTALLED_CONTROLLER_LAUNCH_IDENTITY_DOMAIN);
    digest.update(source_binding_sha256);
    digest.update(process_id.to_be_bytes());
    digest.update(process_creation_time.to_be_bytes());
    digest.update(session_id.to_be_bytes());
    digest.update(controller_file_identity_sha256);
    digest.finalize().into()
}

#[allow(clippy::too_many_arguments)]
fn controller_run_launch_binding(
    request_id: &str,
    source_binding_sha256: &[u8; 32],
    installed_controller_launch_identity_sha256: &[u8; 32],
    generation_sha256: &[u8; 32],
    transaction_sha256: &[u8; 32],
    expected_external_binding_sha256: &[u8; 32],
    peer_binding_sha256: &[u8; 32],
) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(CONTROLLER_RUN_LAUNCH_BINDING_DOMAIN);
    digest.update((request_id.len() as u64).to_be_bytes());
    digest.update(request_id.as_bytes());
    digest.update(source_binding_sha256);
    digest.update(installed_controller_launch_identity_sha256);
    digest.update(generation_sha256);
    digest.update(transaction_sha256);
    digest.update(expected_external_binding_sha256);
    digest.update(peer_binding_sha256);
    digest.update((EXTERNAL_HANDLE_COUNT as u64).to_be_bytes());
    digest.finalize().into()
}

fn is_zero_digest(value: &[u8; 32]) -> bool {
    value.iter().all(|byte| *byte == 0)
}

fn parse_lower_hex_digest(value: &str) -> Result<[u8; 32], ControllerLauncherError> {
    if value.len() != 64
        || !value
            .as_bytes()
            .iter()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(byte))
    {
        return Err(ControllerLauncherError::new(
            "authority_controller_launch_expectation_digest_invalid",
        ));
    }
    let mut decoded = [0u8; 32];
    for (index, chunk) in value.as_bytes().chunks_exact(2).enumerate() {
        decoded[index] = (lower_hex_nibble(chunk[0]) << 4) | lower_hex_nibble(chunk[1]);
    }
    if is_zero_digest(&decoded) {
        return Err(ControllerLauncherError::new(
            "authority_controller_launch_expectation_digest_invalid",
        ));
    }
    Ok(decoded)
}

fn lower_hex_nibble(value: u8) -> u8 {
    match value {
        b'0'..=b'9' => value - b'0',
        b'a'..=b'f' => value - b'a' + 10,
        _ => unreachable!("lowercase hex was validated before decoding"),
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

fn wide_null(value: &OsStr) -> Vec<u16> {
    value.encode_wide().chain(std::iter::once(0)).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::primitive_evidence_authority_windows::{AUTHORITY_PIPE_NAME, AUTHORITY_PIPE_SDDL};
    use serde_json::json;
    use std::{
        fs,
        io::Write,
        os::windows::io::AsHandle,
        sync::atomic::{AtomicU64, Ordering},
    };
    use windows_sys::Win32::{
        Foundation::{CloseHandle, SetHandleInformation, WAIT_OBJECT_0, WAIT_TIMEOUT},
        System::Threading::{GetCurrentProcess, GetProcessHandleCount, WaitForSingleObject},
    };

    struct ExternalFiles {
        root: PathBuf,
        files: Option<[File; EXTERNAL_HANDLE_COUNT]>,
    }

    impl ExternalFiles {
        fn new() -> Self {
            static SEQUENCE: AtomicU64 = AtomicU64::new(1);
            let root = std::env::temp_dir().join(format!(
                "vrcforge-controller-launcher-{}-{}-{}",
                std::process::id(),
                SEQUENCE.fetch_add(1, Ordering::Relaxed),
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap()
                    .as_nanos()
            ));
            fs::create_dir(&root).unwrap();
            let mut files = Vec::new();
            for index in 0..EXTERNAL_HANDLE_COUNT {
                let path = root.join(format!("role-{index}.bin"));
                let mut writer = OpenOptions::new()
                    .create_new(true)
                    .write(true)
                    .open(&path)
                    .unwrap();
                writer.write_all(&[index as u8 + 1]).unwrap();
                writer.flush().unwrap();
                drop(writer);
                files.push(
                    OpenOptions::new()
                        .read(true)
                        .share_mode(FILE_SHARE_READ)
                        .open(path)
                        .unwrap(),
                );
            }
            Self {
                root,
                files: Some(files.try_into().unwrap()),
            }
        }

        fn handles(&self) -> [BorrowedHandle<'_>; EXTERNAL_HANDLE_COUNT] {
            self.files
                .as_ref()
                .expect("external test files remain owned")
                .each_ref()
                .map(File::as_handle)
        }

        fn files(&self) -> &[File; EXTERNAL_HANDLE_COUNT] {
            self.files
                .as_ref()
                .expect("external test files remain owned")
        }
    }

    impl Drop for ExternalFiles {
        fn drop(&mut self) {
            drop(self.files.take());
            let _ = fs::remove_dir_all(&self.root);
        }
    }

    fn request() -> ControllerRunLaunchRequest {
        ControllerRunLaunchRequest::new("request-1".to_owned(), [0x74; 32], [0x75; 32]).unwrap()
    }

    fn synthetic_expectation() -> ControllerLaunchAdmissionExpectation {
        let mut expectation = ControllerLaunchAdmissionExpectation {
            request_id: "request-1".to_owned(),
            generation_sha256: [0x44; 32],
            transaction_sha256: [0x46; 32],
            expected_external_binding_sha256: [0x47; 32],
            source_binding_sha256: [0x48; 32],
            installed_controller_launch_identity_sha256: [0; 32],
            launch_binding_sha256: [0; 32],
            process_id: 11,
            process_creation_time: 12,
            session_id: 13,
            controller_sha256: [0x41; 32],
            controller_file_identity_sha256: [0x42; 32],
            peer_binding_sha256: [0; 32],
        };
        rebind_synthetic_expectation(&mut expectation);
        expectation
    }

    fn rebind_synthetic_expectation(expectation: &mut ControllerLaunchAdmissionExpectation) {
        expectation.installed_controller_launch_identity_sha256 =
            installed_controller_launch_identity(
                &expectation.source_binding_sha256,
                expectation.process_id,
                expectation.process_creation_time,
                expectation.session_id,
                &expectation.controller_file_identity_sha256,
            );
        expectation.peer_binding_sha256 = controller_peer_binding(
            expectation.process_id,
            expectation.process_creation_time,
            expectation.session_id,
            &expectation.controller_sha256,
            &expectation.controller_file_identity_sha256,
        );
        expectation.launch_binding_sha256 = controller_run_launch_binding(
            &expectation.request_id,
            &expectation.source_binding_sha256,
            &expectation.installed_controller_launch_identity_sha256,
            &expectation.generation_sha256,
            &expectation.transaction_sha256,
            &expectation.expected_external_binding_sha256,
            &expectation.peer_binding_sha256,
        );
    }

    #[test]
    fn request_and_source_require_exact_nonzero_bindings() {
        for (request_id, transaction, binding) in [
            ("", [0x74; 32], [0x75; 32]),
            ("-bad", [0x74; 32], [0x75; 32]),
            ("request-1", [0; 32], [0x75; 32]),
            ("request-1", [0x74; 32], [0; 32]),
        ] {
            assert!(
                ControllerRunLaunchRequest::new(request_id.to_owned(), transaction, binding)
                    .is_err()
            );
        }
        let current_path = std::env::current_exe().unwrap();
        let current_file = open_controller_file(&current_path).unwrap();
        let (current_sha256, current_identity) = hash_held_controller(&current_file).unwrap();
        assert_eq!(
            AuthenticatedControllerLaunchSource::from_held_source(
                current_file,
                current_path,
                current_sha256,
                current_identity.byte_length,
                current_identity.volume_serial,
                current_identity.file_id,
                current_identity.link_count,
                [0x71; 32],
                [0x72; 32],
                u32::MAX,
                1,
                [0x73; 32],
                false,
            )
            .unwrap_err()
            .code(),
            "authority_controller_launch_path_invalid"
        );
        let mut source =
            AuthenticatedControllerLaunchSource::for_current_test_executable().unwrap();
        source.verify_still_stable().unwrap();
        let debug = format!("{source:?}");
        assert!(!debug.contains(&hex_lower(&source.generation_sha256)));
        assert!(!debug.contains(&hex_lower(&source.signer_key_id)));
    }

    #[test]
    fn exact_startup_handle_list_inherits_only_the_external_six_objects() {
        let files = ExternalFiles::new();
        // Warm the process-creation APIs before taking the leak baseline;
        // Windows may lazily initialize process-local bookkeeping on the first
        // CreateProcess call in this test executable.
        let warm = begin_authenticated_controller_run_launch(
            AuthenticatedControllerLaunchSource::for_current_test_executable().unwrap(),
            request(),
            files.handles(),
        )
        .unwrap();
        assert_eq!(
            warm.resume_and_exchange().unwrap_err().code(),
            PRODUCTION_SIGNED_EXCHANGE_BLOCKER
        );
        let before = process_handle_count();
        let unrelated = unsafe {
            windows_sys::Win32::System::Threading::CreateEventW(ptr::null(), 1, 0, ptr::null())
        };
        assert!(!unrelated.is_null());
        assert_ne!(
            unsafe { SetHandleInformation(unrelated, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT) },
            0
        );
        let pending = begin_authenticated_controller_run_launch(
            AuthenticatedControllerLaunchSource::for_current_test_executable().unwrap(),
            request(),
            files.handles(),
        )
        .unwrap();
        assert_eq!(
            pending.inherited_wire_values_for_test().len(),
            EXTERNAL_HANDLE_COUNT
        );
        let expectation = pending.admission_expectation();
        let expectation_bytes = expectation.canonical_bytes().unwrap();
        let decoded = ControllerLaunchAdmissionExpectation::decode_canonical(&expectation_bytes)
            .expect("strict expectation round trip");
        assert_eq!(decoded, expectation);
        assert!(!is_zero_digest(&expectation.digest().unwrap()));
        assert_eq!(decoded.request_id(), "request-1");
        assert_eq!(
            decoded.generation_sha256(),
            &pending.source.generation_sha256
        );
        assert_eq!(
            decoded.transaction_sha256(),
            &pending.request.transaction_sha256
        );
        assert_eq!(
            decoded.expected_external_binding_sha256(),
            &pending.request.expected_external_binding_sha256
        );
        assert_eq!(
            decoded.source_binding_sha256(),
            &pending.source.source_binding_sha256
        );
        assert_eq!(
            decoded.installed_controller_launch_identity_sha256(),
            &installed_controller_launch_identity(
                &pending.source.source_binding_sha256,
                pending.evidence.process_id,
                pending.evidence.creation_time,
                pending.evidence.session_id,
                &pending.evidence.controller_file_identity_sha256,
            )
        );
        assert_eq!(
            decoded.launch_binding_sha256(),
            &pending.launch_binding_sha256
        );
        assert_eq!(decoded.process_id(), pending.evidence.process_id);
        assert_eq!(
            decoded.process_creation_time(),
            pending.evidence.creation_time
        );
        assert_eq!(decoded.session_id(), pending.evidence.session_id);
        assert_eq!(
            decoded.controller_sha256(),
            &pending.evidence.controller_sha256
        );
        assert_eq!(
            decoded.controller_file_identity_sha256(),
            &pending.evidence.controller_file_identity_sha256
        );
        assert_eq!(
            decoded.peer_binding_sha256(),
            &pending.evidence.peer_binding_sha256
        );
        let wire: ControllerLaunchAdmissionExpectationWire =
            serde_json::from_slice(&expectation_bytes).unwrap();
        assert_eq!(wire.canonical_byte_length as usize, expectation_bytes.len());
        assert!(expectation_bytes.len() <= MAX_CONTROLLER_ADMISSION_EXPECTATION_BYTES);
        assert_eq!(
            expectation_bytes
                .windows(CONTROLLER_ADMISSION_EXPECTATION_SCHEMA.len())
                .filter(|window| *window == CONTROLLER_ADMISSION_EXPECTATION_SCHEMA.as_bytes())
                .count(),
            1
        );
        for raw in pending.inherited_wire_values_for_test() {
            assert!(!expectation_bytes
                .windows(raw.len())
                .any(|window| window == raw.as_bytes()));
        }
        let child = pending.process_raw_for_test();
        let current = unsafe { GetCurrentProcess() };
        for (token, source) in pending
            .inherited_wire_values_for_test()
            .iter()
            .zip(files.files().iter())
        {
            let child_handle = usize::from_str_radix(token, 16).unwrap() as HANDLE;
            let mut parent_probe = null_mut();
            assert_ne!(
                unsafe {
                    DuplicateHandle(
                        child,
                        child_handle,
                        current,
                        &mut parent_probe,
                        0,
                        0,
                        DUPLICATE_SAME_ACCESS,
                    )
                },
                0
            );
            assert_ne!(
                unsafe { CompareObjectHandles(parent_probe, source.as_raw_handle() as HANDLE) },
                0
            );
            assert_ne!(unsafe { CloseHandle(parent_probe) }, 0);
        }

        let mut inherited_job_probe = null_mut();
        let duplicated_job = unsafe {
            DuplicateHandle(
                child,
                pending.containment_job_raw_for_test(),
                current,
                &mut inherited_job_probe,
                0,
                0,
                DUPLICATE_SAME_ACCESS,
            )
        };
        if duplicated_job != 0 {
            assert_eq!(
                unsafe {
                    CompareObjectHandles(
                        inherited_job_probe,
                        pending.containment_job_raw_for_test(),
                    )
                },
                0,
                "the containment job handle must not be inherited"
            );
            assert_ne!(unsafe { CloseHandle(inherited_job_probe) }, 0);
        }

        let mut unexpected_probe = null_mut();
        let duplicated = unsafe {
            DuplicateHandle(
                child,
                unrelated,
                current,
                &mut unexpected_probe,
                0,
                0,
                DUPLICATE_SAME_ACCESS,
            )
        };
        if duplicated != 0 {
            assert_eq!(
                unsafe { CompareObjectHandles(unexpected_probe, unrelated) },
                0
            );
            assert_ne!(unsafe { CloseHandle(unexpected_probe) }, 0);
        }
        assert_ne!(unsafe { CloseHandle(unrelated) }, 0);
        assert_eq!(
            pending.resume_and_exchange().unwrap_err().code(),
            PRODUCTION_SIGNED_EXCHANGE_BLOCKER
        );
        assert_eq!(process_handle_count(), before);
    }

    #[test]
    fn kill_on_close_job_contains_child_without_direct_terminate_or_wait_cleanup() {
        let files = ExternalFiles::new();
        let pending = begin_authenticated_controller_run_launch(
            AuthenticatedControllerLaunchSource::for_current_test_executable().unwrap(),
            request(),
            files.handles(),
        )
        .unwrap();
        assert_eq!(
            unsafe { WaitForSingleObject(pending.process_raw_for_test(), 0) },
            WAIT_TIMEOUT,
            "controller must still be suspended before hostile cleanup"
        );
        let mut in_job = 0;
        assert_ne!(
            unsafe {
                IsProcessInJob(
                    pending.process_raw_for_test(),
                    pending.containment_job_raw_for_test(),
                    &mut in_job,
                )
            },
            0
        );
        assert_ne!(in_job, 0);

        let current = unsafe { GetCurrentProcess() };
        let mut process_probe = null_mut();
        assert_ne!(
            unsafe {
                DuplicateHandle(
                    current,
                    pending.process_raw_for_test(),
                    current,
                    &mut process_probe,
                    0,
                    0,
                    DUPLICATE_SAME_ACCESS,
                )
            },
            0
        );
        let process_probe = unsafe { OwnedHandle::from_raw_handle(process_probe as RawHandle) };

        // No direct TerminateProcess/Wait cleanup path is invoked by Drop.
        // Closing the sole job handle must contain the child independently.
        drop(pending);
        assert_eq!(
            unsafe { WaitForSingleObject(process_probe.as_raw_handle() as HANDLE, 5_000,) },
            WAIT_OBJECT_0
        );
    }

    #[test]
    fn admission_expectation_rejects_length_case_shape_and_binding_drift() {
        let files = ExternalFiles::new();
        let pending = begin_authenticated_controller_run_launch(
            AuthenticatedControllerLaunchSource::for_current_test_executable().unwrap(),
            request(),
            files.handles(),
        )
        .unwrap();
        let expectation = pending.admission_expectation();
        let canonical = expectation.canonical_bytes().unwrap();

        let mut trailing = canonical.clone();
        trailing.push(b' ');
        assert_eq!(
            ControllerLaunchAdmissionExpectation::decode_canonical(&trailing)
                .unwrap_err()
                .code(),
            "authority_controller_launch_expectation_shape_invalid"
        );

        let generation = hex_lower(expectation.generation_sha256());
        let generation_offset = canonical
            .windows(generation.len())
            .position(|window| window == generation.as_bytes())
            .unwrap();
        let mut uppercase = canonical.clone();
        uppercase[generation_offset] = b'A';
        assert_eq!(
            ControllerLaunchAdmissionExpectation::decode_canonical(&uppercase)
                .unwrap_err()
                .code(),
            "authority_controller_launch_expectation_digest_invalid"
        );

        let assert_binding_rejected = |drifted: ControllerLaunchAdmissionExpectation| {
            assert_eq!(
                ControllerLaunchAdmissionExpectation::decode_canonical(
                    &drifted.canonical_bytes().unwrap(),
                )
                .unwrap_err()
                .code(),
                "authority_controller_launch_expectation_binding_invalid"
            );
        };

        let mut request_drift = expectation.clone();
        request_drift.request_id = "request-2".to_owned();
        assert_binding_rejected(request_drift);

        let mut generation_drift = expectation.clone();
        generation_drift.generation_sha256[0] ^= 1;
        assert_binding_rejected(generation_drift);

        let mut transaction_drift = expectation.clone();
        transaction_drift.transaction_sha256[0] ^= 1;
        assert_binding_rejected(transaction_drift);

        let mut external_drift = expectation.clone();
        external_drift.expected_external_binding_sha256[0] ^= 1;
        assert_binding_rejected(external_drift);

        let mut source_drift = expectation.clone();
        source_drift.source_binding_sha256[0] ^= 1;
        source_drift.installed_controller_launch_identity_sha256 =
            installed_controller_launch_identity(
                &source_drift.source_binding_sha256,
                source_drift.process_id,
                source_drift.process_creation_time,
                source_drift.session_id,
                &source_drift.controller_file_identity_sha256,
            );
        assert_binding_rejected(source_drift);

        let mut installed_identity_drift = expectation.clone();
        installed_identity_drift.installed_controller_launch_identity_sha256[0] ^= 1;
        assert_binding_rejected(installed_identity_drift);

        let mut peer_constituent_drift = expectation.clone();
        peer_constituent_drift.controller_sha256[0] ^= 1;
        peer_constituent_drift.peer_binding_sha256 = controller_peer_binding(
            peer_constituent_drift.process_id,
            peer_constituent_drift.process_creation_time,
            peer_constituent_drift.session_id,
            &peer_constituent_drift.controller_sha256,
            &peer_constituent_drift.controller_file_identity_sha256,
        );
        assert_binding_rejected(peer_constituent_drift);

        let mut launch_drift = expectation.clone();
        launch_drift.launch_binding_sha256[0] ^= 1;
        assert_binding_rejected(launch_drift);

        let mut duplicate_schema = String::from_utf8(canonical.clone()).unwrap();
        duplicate_schema =
            duplicate_schema.replacen("\"schema\":", "\"schema\":\"duplicate\",\"schema\":", 1);
        assert_eq!(
            ControllerLaunchAdmissionExpectation::decode_canonical(duplicate_schema.as_bytes())
                .unwrap_err()
                .code(),
            "authority_controller_launch_expectation_decode_failed"
        );
        assert_eq!(
            ControllerLaunchAdmissionExpectation::decode_canonical(&vec![
                b'x';
                MAX_CONTROLLER_ADMISSION_EXPECTATION_BYTES
                    + 1
            ])
            .unwrap_err()
            .code(),
            "authority_controller_launch_expectation_length_invalid"
        );

        let wire_text = String::from_utf8(canonical).unwrap().to_ascii_lowercase();
        for prohibited in ["rawhandle", "controllerpath", "signerkey", "privatekey"] {
            assert!(!wire_text.contains(prohibited));
        }
    }

    #[test]
    fn alias_failure_burns_partial_duplicates_without_handle_leak() {
        let files = ExternalFiles::new();
        let before = process_handle_count();
        let mut handles = files.handles();
        handles[5] = handles[0];
        let error = match InheritableExternalSix::duplicate_from(handles) {
            Ok(_) => panic!("aliased external handle set must fail"),
            Err(error) => error,
        };
        assert_eq!(error.code(), "authority_controller_external_handle_alias");
        assert_eq!(process_handle_count(), before);
    }

    #[test]
    fn command_line_contains_only_fixed_expectations_and_exact_six_tokens() {
        let source = AuthenticatedControllerLaunchSource::for_current_test_executable().unwrap();
        let request = request();
        let values = std::array::from_fn(|index| format!("{:016x}", 0x100 + index));
        let command = controller_command_line(&source, &request, &values).unwrap();
        let command = String::from_utf16(&command[..command.len() - 1]).unwrap();
        assert!(command.contains("--run-model-part-composition"));
        assert!(command.contains(&hex_lower(&source.generation_sha256)));
        assert!(command.contains(&hex_lower(&request.transaction_sha256)));
        assert!(command.contains(&hex_lower(&request.expected_external_binding_sha256)));
        for value in values {
            assert_eq!(command.matches(&value).count(), 1);
        }
        for prohibited in ["--raw", "--sign", "--handle-path", "--controller-path"] {
            assert!(!command.contains(prohibited));
        }
    }

    #[test]
    fn output_binding_rejects_nonzero_exit_noncanonical_and_unbound_payloads() {
        struct RejectVerifier;
        impl AuthorityHandshakeSignatureVerifier for RejectVerifier {
            fn verify_digest_signature(
                &mut self,
                _signer_key_id: &[u8; 32],
                _digest: &[u8; 32],
                _signature: &[u8; 64],
            ) -> Result<(), AuthorityClientError> {
                Err(AuthorityClientError::from_code(
                    "test_signature_verifier_rejected",
                ))
            }
        }
        let envelope = ControllerExchangeEnvelope {
            schema: CONTROLLER_EXCHANGE_SCHEMA.to_owned(),
            command: "runModelPartComposition".to_owned(),
            requires_upper_layer_verification: true,
            handshake_raw_json: "{}".to_owned(),
            response_raw_json: "{}".to_owned(),
        };
        let mut output = serde_json::to_vec(&envelope).unwrap();
        output.push(b'\n');
        let base = CompletedControllerProcess {
            expectation: synthetic_expectation(),
            signer_key_id: [0x45; 32],
            command: "runModelPartComposition",
            exit_code: 0,
            output,
        };
        let mut nonzero = base.clone();
        nonzero.exit_code = 2;
        assert_eq!(
            parse_unverified_completed_controller_exchange(nonzero, &mut RejectVerifier)
                .unwrap_err()
                .code(),
            "authority_controller_process_completion_invalid"
        );
        let mut extra_newline = base.clone();
        extra_newline.output.push(b'\n');
        assert_eq!(
            parse_unverified_completed_controller_exchange(extra_newline, &mut RejectVerifier)
                .unwrap_err()
                .code(),
            "authority_controller_output_termination_invalid"
        );
        assert_eq!(
            parse_unverified_completed_controller_exchange(base, &mut RejectVerifier)
                .unwrap_err()
                .code(),
            "authority_client_response_schema_mismatch"
        );
    }

    #[test]
    fn completed_output_remains_affine_unverified_without_a_response_signature() {
        const GENERATION: [u8; 32] = [0x44; 32];
        const SIGNER: [u8; 32] = [0x45; 32];
        let expectation = synthetic_expectation();
        let peer = *expectation.peer_binding_sha256();
        let (handshake, attestation_digest, signature) = signed_handshake(GENERATION, SIGNER, peer);
        let response = serde_json::to_string(&json!({
            "command": "runModelPartComposition",
            "ok": true,
            "result": {"accepted": false},
            "schema": "vrcforge.primitive_evidence_authority_response.v1",
        }))
        .unwrap();
        let envelope = ControllerExchangeEnvelope {
            schema: CONTROLLER_EXCHANGE_SCHEMA.to_owned(),
            command: "runModelPartComposition".to_owned(),
            requires_upper_layer_verification: true,
            handshake_raw_json: handshake,
            response_raw_json: response.clone(),
        };
        let mut output = serde_json::to_vec(&envelope).unwrap();
        output.push(b'\n');
        let completed = CompletedControllerProcess {
            expectation: expectation.clone(),
            signer_key_id: SIGNER,
            command: "runModelPartComposition",
            exit_code: 0,
            output,
        };
        struct ExactVerifier {
            calls: usize,
            digest: [u8; 32],
            signature: [u8; 64],
        }
        impl AuthorityHandshakeSignatureVerifier for ExactVerifier {
            fn verify_digest_signature(
                &mut self,
                signer_key_id: &[u8; 32],
                digest: &[u8; 32],
                signature: &[u8; 64],
            ) -> Result<(), AuthorityClientError> {
                self.calls += 1;
                if signer_key_id != &SIGNER
                    || digest != &self.digest
                    || signature != &self.signature
                {
                    return Err(AuthorityClientError::from_code(
                        "test_signature_verifier_rejected",
                    ));
                }
                Ok(())
            }
        }
        let mut verifier = ExactVerifier {
            calls: 0,
            digest: attestation_digest,
            signature,
        };
        let unverified =
            parse_unverified_completed_controller_exchange(completed.clone(), &mut verifier)
                .unwrap();
        assert_eq!(verifier.calls, 1);
        assert_eq!(unverified.handshake.peer_binding_sha256(), &peer);
        assert_eq!(unverified.handshake.generation_sha256(), &GENERATION);
        assert!(!is_zero_digest(
            unverified.handshake.canonical_handshake_sha256()
        ));
        assert_eq!(unverified.response_bytes, response.as_bytes());
        assert!(!is_zero_digest(&unverified.transcript.digest_sha256));
        let transcript: serde_json::Value =
            serde_json::from_slice(&unverified.transcript.canonical_bytes).unwrap();
        assert_eq!(transcript["schema"], CONTROLLER_RESPONSE_TRANSCRIPT_SCHEMA);
        assert_eq!(transcript["command"], "runModelPartComposition");
        assert_eq!(transcript["requestId"], expectation.request_id());
        assert_eq!(transcript["processId"], expectation.process_id());
        assert_eq!(
            transcript["processCreationTime"],
            expectation.process_creation_time()
        );
        assert_eq!(transcript["sessionId"], expectation.session_id());
        assert_eq!(
            transcript["controllerSha256"],
            hex_lower(expectation.controller_sha256())
        );
        assert_eq!(
            transcript["controllerFileIdentitySha256"],
            hex_lower(expectation.controller_file_identity_sha256())
        );
        assert_eq!(
            transcript["generationSha256"],
            hex_lower(expectation.generation_sha256())
        );
        assert_eq!(
            transcript["signerKeyId"],
            hex_lower(unverified.handshake.signer_key_id())
        );
        assert_eq!(
            transcript["canonicalHandshakeSha256"],
            hex_lower(unverified.handshake.canonical_handshake_sha256())
        );
        assert_eq!(
            transcript["handshakeAttestationSha256"],
            hex_lower(unverified.handshake.attestation_digest())
        );
        assert_eq!(
            transcript["handshakeSignatureP256"],
            hex_lower(unverified.handshake.signature_p256())
        );
        assert_eq!(
            transcript["launchBindingSha256"],
            hex_lower(expectation.launch_binding_sha256())
        );

        let base_transcript_digest = unverified.transcript.digest_sha256;
        let changed_response = serde_json::to_string(&json!({
            "command": "runModelPartComposition",
            "ok": true,
            "result": {"accepted": true},
            "schema": "vrcforge.primitive_evidence_authority_response.v1",
        }))
        .unwrap();
        let changed_response_transcript = unsigned_controller_response_transcript(
            &expectation,
            "runModelPartComposition",
            &SIGNER,
            &unverified.handshake,
            changed_response.as_bytes(),
        )
        .unwrap();
        assert_ne!(
            changed_response_transcript.digest_sha256,
            base_transcript_digest
        );

        let mut request_drift = expectation.clone();
        request_drift.request_id = "request-2".to_owned();
        rebind_synthetic_expectation(&mut request_drift);
        let changed_request_transcript = unsigned_controller_response_transcript(
            &request_drift,
            "runModelPartComposition",
            &SIGNER,
            &unverified.handshake,
            response.as_bytes(),
        )
        .unwrap();
        assert_ne!(
            changed_request_transcript.digest_sha256,
            base_transcript_digest
        );

        let mut transaction_drift = expectation.clone();
        transaction_drift.transaction_sha256[0] ^= 1;
        rebind_synthetic_expectation(&mut transaction_drift);
        let changed_transaction_transcript = unsigned_controller_response_transcript(
            &transaction_drift,
            "runModelPartComposition",
            &SIGNER,
            &unverified.handshake,
            response.as_bytes(),
        )
        .unwrap();
        assert_ne!(
            changed_transaction_transcript.digest_sha256,
            base_transcript_digest
        );

        let mut generation_drift = expectation.clone();
        generation_drift.generation_sha256[0] ^= 1;
        rebind_synthetic_expectation(&mut generation_drift);
        assert_eq!(
            unsigned_controller_response_transcript(
                &generation_drift,
                "runModelPartComposition",
                &SIGNER,
                &unverified.handshake,
                response.as_bytes(),
            )
            .unwrap_err()
            .code(),
            "authority_controller_response_transcript_input_invalid"
        );
        let mut signer_drift = SIGNER;
        signer_drift[0] ^= 1;
        assert_eq!(
            unsigned_controller_response_transcript(
                &expectation,
                "runModelPartComposition",
                &signer_drift,
                &unverified.handshake,
                response.as_bytes(),
            )
            .unwrap_err()
            .code(),
            "authority_controller_response_transcript_input_invalid"
        );
        assert_eq!(
            unsigned_controller_response_transcript(
                &expectation,
                "status",
                &SIGNER,
                &unverified.handshake,
                response.as_bytes(),
            )
            .unwrap_err()
            .code(),
            "authority_controller_response_transcript_input_invalid"
        );

        let mut drifted = completed;
        drifted.expectation.peer_binding_sha256[0] ^= 1;
        let mut verifier = ExactVerifier {
            calls: 0,
            digest: attestation_digest,
            signature,
        };
        assert_eq!(
            parse_unverified_completed_controller_exchange(drifted, &mut verifier)
                .unwrap_err()
                .code(),
            "authority_controller_launch_expectation_binding_invalid"
        );
        assert_eq!(verifier.calls, 0);
    }

    #[test]
    fn source_keeps_production_resume_closed_and_uses_exact_attribute_api() {
        let source = include_str!("primitive_evidence_controller_launcher_windows.rs");
        for required in [
            "STARTUPINFOEXW",
            "PROC_THREAD_ATTRIBUTE_HANDLE_LIST",
            "InitializeProcThreadAttributeList",
            "UpdateProcThreadAttribute",
            "CREATE_SUSPENDED",
            "PROC_THREAD_ATTRIBUTE_JOB_LIST",
            "JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE",
            "IsProcessInJob",
            "verify_parent_controller_exchange",
            "UnverifiedControllerExchange",
            "UnsignedControllerResponseTranscript",
            "CONTROLLER_RESPONSE_TRANSCRIPT_DOMAIN",
            "pub(crate) enum VerifiedControllerExchange {}",
        ] {
            assert!(source.contains(required));
        }
        for prohibited in [
            ["std::process::", "Command"].concat(),
            ["std::env::", "var"].concat(),
            ["Stdio", "::piped"].concat(),
            ["pub(crate) fn ", "from_held_source"].concat(),
            ["from_authenticated", "_held_source"].concat(),
        ] {
            assert!(!source.contains(&prohibited));
        }
        assert!(source.contains("    fn from_held_source("));
        let direct_terminate_call = ["Terminate", "Process("].concat();
        assert!(!source.contains(&direct_terminate_call));
        let verified_success_construction = ["Ok(", "VerifiedControllerExchange {"].concat();
        let implicit_conversion = ["impl From<Unverified", "ControllerExchange>"].concat();
        assert!(!source.contains(&verified_success_construction));
        assert!(!source.contains(&implicit_conversion));
        assert!(source.contains(PRODUCTION_SIGNED_EXCHANGE_BLOCKER));
    }

    fn process_handle_count() -> u32 {
        let mut count = 0u32;
        assert_ne!(
            unsafe { GetProcessHandleCount(GetCurrentProcess(), &mut count) },
            0
        );
        count
    }

    fn signed_handshake(
        generation: [u8; 32],
        signer_key_id: [u8; 32],
        peer_binding: [u8; 32],
    ) -> (String, [u8; 32], [u8; 64]) {
        const FIXED_PIPE_IDENTITY_DOMAIN: &[u8] = b"vrcforge-authority-fixed-pipe-identity-v1\0";
        const SERVICE_INSTANCE_DOMAIN: &[u8] = b"vrcforge-authority-service-instance-v1\0";
        const ATTESTATION_DOMAIN: &[u8] = b"vrcforge-authority-generation-attestation-v1\0";
        const POLICY_ID: &str = "vrcforge.authority.generation-attestation.fixed.v1";
        const PROOF_ALGORITHM: &str = "p256-sha256-raw-rs-low-s";
        let challenge = [0x51; 32];
        let service_executable = [0x52; 32];
        let service_executable_path = [0x53; 32];
        let service_executable_file_identity = [0x54; 32];
        let protected_manifest = [0x55; 32];
        let protected_key = [0x56; 32];
        let protected_ledger = [0x57; 32];
        let scm_readback = [0x58; 32];
        let bootstrap_receipt = [0x59; 32];
        let service_process_id = 61u32;
        let service_process_started_at = 62u64;
        let mut fixed = Sha256::new();
        fixed.update(FIXED_PIPE_IDENTITY_DOMAIN);
        fixed.update(AUTHORITY_PIPE_NAME.as_bytes());
        fixed.update(AUTHORITY_PIPE_SDDL.as_bytes());
        fixed.update((64 * 1024u64).to_be_bytes());
        fixed.update((16 * 1024 * 1024u64).to_be_bytes());
        fixed.update(POLICY_ID.as_bytes());
        let fixed_pipe_identity: [u8; 32] = fixed.finalize().into();
        let mut service = Sha256::new();
        service.update(SERVICE_INSTANCE_DOMAIN);
        service.update(generation);
        service.update(service_executable);
        service.update(service_executable_path);
        service.update(service_executable_file_identity);
        service.update(service_process_id.to_be_bytes());
        service.update(service_process_started_at.to_be_bytes());
        service.update(fixed_pipe_identity);
        service.update(protected_manifest);
        service.update(protected_key);
        service.update(signer_key_id);
        service.update(protected_ledger);
        service.update(scm_readback);
        service.update(bootstrap_receipt);
        let service_instance: [u8; 32] = service.finalize().into();
        let sequence = 1u64;
        let mut attestation = Sha256::new();
        attestation.update(ATTESTATION_DOMAIN);
        attestation.update(POLICY_ID.as_bytes());
        attestation.update(PROOF_ALGORITHM.as_bytes());
        attestation.update(fixed_pipe_identity);
        attestation.update(service_instance);
        attestation.update(peer_binding);
        attestation.update(challenge);
        attestation.update(sequence.to_be_bytes());
        let attestation_digest: [u8; 32] = attestation.finalize().into();
        let mut signature = [0u8; 64];
        signature[31] = 1;
        signature[63] = 1;
        let payload = serde_json::to_string(&json!({
            "command": "handshake",
            "ok": true,
            "result": {
                "attestationDigest": hex_lower(&attestation_digest),
                "bootstrapReceiptSha256": hex_lower(&bootstrap_receipt),
                "challenge": hex_lower(&challenge),
                "currentGeneration": hex_lower(&generation),
                "fixedPipeIdentityDigest": hex_lower(&fixed_pipe_identity),
                "peerBindingSha256": hex_lower(&peer_binding),
                "pipeName": AUTHORITY_PIPE_NAME,
                "policyId": POLICY_ID,
                "proofAlgorithm": PROOF_ALGORITHM,
                "protectedKeyReadbackSha256": hex_lower(&protected_key),
                "protectedLedgerReadbackSha256": hex_lower(&protected_ledger),
                "protectedManifestReadbackSha256": hex_lower(&protected_manifest),
                "schema": "vrcforge.primitive_evidence_authority_generation_attestation.v1",
                "scmReadbackSha256": hex_lower(&scm_readback),
                "sequence": sequence,
                "serviceExecutableFileIdentitySha256": hex_lower(&service_executable_file_identity),
                "serviceExecutablePathSha256": hex_lower(&service_executable_path),
                "serviceExecutableSha256": hex_lower(&service_executable),
                "serviceInstanceDigest": hex_lower(&service_instance),
                "serviceProcessId": service_process_id,
                "serviceProcessStartedAt": service_process_started_at,
                "signatureP256": hex_lower(&signature),
                "signerKeyId": hex_lower(&signer_key_id),
            },
            "schema": "vrcforge.primitive_evidence_authority_response.v1",
        }))
        .unwrap();
        (payload, attestation_digest, signature)
    }
}

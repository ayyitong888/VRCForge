//! Child-owned authenticated startup over the fixed inherited pipe set.
//!
//! This module is a child of the pure protocol module so only this Windows
//! adapter can construct the private control-peer witness after kernel-backed
//! observations. It never derives authority identity or generation from the
//! received expectation envelope.

#![cfg(windows)]
#![cfg_attr(not(test), allow(dead_code))]

use super::{
    AuthenticatedControlPeerWitness, BootstrapDigest, ChildBootstrapRole, ChildImageContextDigest,
    ChildObservedLaunchContextDigest, ChildTransportContractContextDigest,
    ControlServerIdentityContextDigest, MinimalEnvironmentContextDigest, PreparedChildReady,
    RunnerTokenContextDigest, ValidatedChildBootstrap,
};
use crate::primitive_evidence_child_transport_windows::{
    AuthenticatedChildRuntimeTransport, ChildHandshakeTransport, ChildStandardHandleError,
    ValidatedChildStandardHandleSet,
};
use crate::primitive_evidence_process_token_windows::{
    measure_expected_restricted_runner_token_digest, measure_process_token_digest,
    require_thread_without_impersonation_token, ExpectedRestrictedRunnerSid, ProcessTokenPolicy,
};
use sha2::{Digest as Sha2Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    ffi::{OsStr, OsString},
    fmt,
    fs::{File, OpenOptions},
    io::{self, Read, Seek, SeekFrom, Write},
    mem::{size_of, zeroed},
    os::windows::{
        ffi::{OsStrExt, OsStringExt},
        fs::{MetadataExt, OpenOptionsExt},
        io::{AsRawHandle, FromRawHandle, OwnedHandle, RawHandle},
    },
    path::{Path, PathBuf},
    ptr::{self, null_mut},
    sync::atomic::{compiler_fence, Ordering},
    time::{Duration, Instant},
};
use windows_sys::Win32::{
    Foundation::{
        GetLastError, ERROR_BROKEN_PIPE, ERROR_IO_PENDING, ERROR_OPERATION_ABORTED, FILETIME,
        HANDLE, INVALID_HANDLE_VALUE, STILL_ACTIVE, WAIT_OBJECT_0, WAIT_TIMEOUT,
    },
    Storage::FileSystem::{
        GetFileInformationByHandle, GetFinalPathNameByHandleW, ReadFile, WriteFile,
        BY_HANDLE_FILE_INFORMATION, FILE_ATTRIBUTE_DIRECTORY, FILE_ATTRIBUTE_REPARSE_POINT,
        FILE_FLAG_OPEN_REPARSE_POINT, FILE_FLAG_SEQUENTIAL_SCAN, FILE_SHARE_DELETE,
        FILE_SHARE_READ, FILE_SHARE_WRITE,
    },
    System::{
        JobObjects::{
            IsProcessInJob, JobObjectBasicAccountingInformation, JobObjectBasicProcessIdList,
            JobObjectExtendedLimitInformation, QueryInformationJobObject,
            JOBOBJECT_BASIC_ACCOUNTING_INFORMATION, JOBOBJECT_BASIC_PROCESS_ID_LIST,
            JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_BREAKAWAY_OK,
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK,
        },
        Threading::{
            CreateEventW, GetCurrentProcessId, GetCurrentThreadId, GetExitCodeProcess,
            GetProcessId, GetProcessTimes, GetThreadTimes, OpenProcess, OpenThread,
            QueryFullProcessImageNameW, WaitForSingleObject, PROCESS_QUERY_LIMITED_INFORMATION,
            THREAD_QUERY_LIMITED_INFORMATION,
        },
        IO::{CancelIoEx, GetOverlappedResult, OVERLAPPED},
    },
};

const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(5);
const CANCEL_SETTLE_MILLIS: u32 = 100;
const PROCESS_SYNCHRONIZE: u32 = 0x0010_0000;
const MAX_IMAGE_BYTES: u64 = 512 * 1024 * 1024;
const MAX_JOB_ROSTER_PROCESSES: usize = 64;
const MAX_ENVIRONMENT_UTF16_UNITS: usize = 32_767;
const AUTHORITY_IMAGE_NAME: &str = "vrcforge_primitive_evidence_service.exe";
const DRIVER_IMAGE_NAME: &str = "vrcforge_primitive_lifecycle_driver.exe";
const BRIDGE_IMAGE_NAME: &str = "vrcforge_primitive_bridge_launcher.exe";

const CHILD_TRANSPORT_OBSERVATION_DOMAIN: &[u8] = b"vrcforge-child-transport-observation-v1\0";
const CHILD_PROCESS_IMAGE_OBSERVATION_DOMAIN: &[u8] =
    b"vrcforge-child-process-image-observation-v1\0";
const PARENT_PROCESS_IMAGE_RECEIPT_IDENTITY_DOMAIN: &[u8] =
    b"vrcforge-parent-process-image-receipt-identity-v1\0";
const CHILD_ENVIRONMENT_BLOCK_OBSERVATION_DOMAIN: &[u8] =
    b"vrcforge-child-environment-block-observation-v1\0";
const CHILD_CONTROL_SERVER_OBSERVATION_DOMAIN: &[u8] =
    b"vrcforge-child-control-server-observation-v1\0";
const CHILD_JOB_OBSERVATION_DOMAIN: &[u8] = b"vrcforge-child-job-observation-v1\0";
const CHILD_OBSERVED_LAUNCH_SOURCE_DOMAIN: &[u8] = b"vrcforge-child-observed-launch-source-v1\0";

const FIXED_ENVIRONMENT_NAMES: [&str; 14] = [
    "APPDATA",
    "COMSPEC",
    "HOMEDRIVE",
    "HOMEPATH",
    "LOCALAPPDATA",
    "PATH",
    "PATHEXT",
    "PROGRAMDATA",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "WINDIR",
];

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ChildHandshakeError(&'static str);

impl ChildHandshakeError {
    fn new(code: &'static str) -> Self {
        Self(code)
    }

    pub(crate) const fn code(&self) -> &'static str {
        self.0
    }
}

impl fmt::Display for ChildHandshakeError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.0)
    }
}

impl std::error::Error for ChildHandshakeError {}

impl From<super::ChildProtocolError> for ChildHandshakeError {
    fn from(error: super::ChildProtocolError) -> Self {
        Self::new(error.code())
    }
}

impl From<ChildStandardHandleError> for ChildHandshakeError {
    fn from(error: ChildStandardHandleError) -> Self {
        Self::new(error.code())
    }
}

/// Linear authenticated startup state. The control and result endpoints remain
/// inseparable from the validated bootstrap until a role runtime consumes it.
pub(crate) struct AuthenticatedChildStartup {
    validated: ValidatedChildBootstrap,
    transport: AuthenticatedChildRuntimeTransport,
}

impl fmt::Debug for AuthenticatedChildStartup {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("AuthenticatedChildStartup")
            .field("role", &self.validated.role())
            .field("transport", &"<redacted>")
            .finish()
    }
}

impl AuthenticatedChildStartup {
    pub(crate) fn into_runtime_parts(
        self,
    ) -> (ValidatedChildBootstrap, AuthenticatedChildRuntimeTransport) {
        (self.validated, self.transport)
    }
}

pub(crate) fn perform_authenticated_bootstrap(
    expected_role: ChildBootstrapRole,
    validated_handles: ValidatedChildStandardHandleSet,
) -> Result<AuthenticatedChildStartup, ChildHandshakeError> {
    let transport = validated_handles.into_handshake_transport()?;
    validate_expected_role(expected_role, transport.role())?;
    let inputs = HandshakeInputs::from_transport(&transport)?;
    let mut evidence = WindowsChildEvidence::capture(&transport)?;
    let mut nonce = [0u8; 32];
    getrandom::fill(&mut nonce)
        .map_err(|_| ChildHandshakeError::new("child_handshake_secure_random_unavailable"))?;
    let deadline = Instant::now()
        .checked_add(HANDSHAKE_TIMEOUT)
        .ok_or_else(|| ChildHandshakeError::new("child_handshake_timeout"))?;

    let validated = {
        let mut control = BoundedOverlappedPipe::new(
            transport.private_control_handle(),
            deadline,
            BrokenPipeSemantics::Error,
        );
        let mut bootstrap = BoundedOverlappedPipe::new(
            transport.bootstrap_read_handle(),
            deadline,
            BrokenPipeSemantics::Eof,
        );
        let result = perform_protocol(&inputs, &mut evidence, &mut control, &mut bootstrap, nonce);
        volatile_zero_bytes(&mut nonce);
        result?
    };
    let transport = transport.into_authenticated_runtime_transport()?;
    Ok(AuthenticatedChildStartup {
        validated,
        transport,
    })
}

fn validate_expected_role(
    expected: ChildBootstrapRole,
    observed: ChildBootstrapRole,
) -> Result<(), ChildHandshakeError> {
    if expected != observed {
        Err(ChildHandshakeError::new("child_handshake_role_unexpected"))
    } else {
        Ok(())
    }
}

struct HandshakeInputs {
    role: ChildBootstrapRole,
    role_capability_set: super::RoleCapabilitySetBinding,
    raw_handle_list_digest: super::RoleRawHandleListDigest,
}

impl HandshakeInputs {
    fn from_transport(transport: &ChildHandshakeTransport) -> Result<Self, ChildHandshakeError> {
        if transport.role_capability_set().role() != transport.role()
            || transport.role_capability_set().raw_handle_list_digest()
                != transport.raw_handle_list_digest().as_bytes()
        {
            return Err(ChildHandshakeError::new(
                "child_handshake_transport_binding_invalid",
            ));
        }
        Ok(Self {
            role: transport.role(),
            role_capability_set: transport.role_capability_set().clone(),
            raw_handle_list_digest: transport.raw_handle_list_digest(),
        })
    }
}

trait ChildEvidenceProof {
    fn contexts(&self) -> &MeasuredChildContexts;
    fn revalidate(&mut self) -> Result<(), ChildHandshakeError>;
}

#[derive(Clone, Copy)]
struct MeasuredChildContexts {
    observed_launch: ChildObservedLaunchContextDigest,
    child_transport: ChildTransportContractContextDigest,
    runner_token: RunnerTokenContextDigest,
    child_image: ChildImageContextDigest,
    minimal_environment: MinimalEnvironmentContextDigest,
    control_server_identity: ControlServerIdentityContextDigest,
}

fn perform_protocol<C, B, E>(
    inputs: &HandshakeInputs,
    evidence: &mut E,
    control: &mut C,
    bootstrap: &mut B,
    nonce: BootstrapDigest,
) -> Result<ValidatedChildBootstrap, ChildHandshakeError>
where
    C: Read + Write,
    B: Read,
    E: ChildEvidenceProof,
{
    evidence.revalidate()?;
    let child_nonce = super::ChildHandshakeNonce::from_fresh_bytes(nonce)?;
    let awaiting = PreparedChildReady::prepare(
        inputs.role,
        child_nonce,
        &inputs.role_capability_set,
        inputs.raw_handle_list_digest,
        evidence.contexts().observed_launch,
    )?
    .write_to(control)?;
    let pending = awaiting.read_expectation_from(control)?;

    evidence.revalidate()?;
    let witness = authenticate_peer_challenge(inputs, evidence.contexts(), &pending)?;
    let authenticated = pending.authenticate(witness)?;
    let validated = authenticated.read_and_validate_bootstrap(bootstrap)?;
    evidence.revalidate()?;
    let validated = validated.prepare_ack()?.write_to(control)?;
    evidence.revalidate()?;
    Ok(validated)
}

fn authenticate_peer_challenge(
    inputs: &HandshakeInputs,
    measured: &MeasuredChildContexts,
    pending: &super::PendingExpectationEnvelope,
) -> Result<AuthenticatedControlPeerWitness, ChildHandshakeError> {
    let challenge = pending.peer_authentication_challenge();
    if challenge.role() != inputs.role {
        return Err(ChildHandshakeError::new(
            "child_handshake_peer_role_unexpected",
        ));
    }
    if challenge.raw_handle_list_digest() != &inputs.raw_handle_list_digest
        || challenge.expected_child_observation_context() != &measured.observed_launch
        || challenge.child_transport_contract_context() != &measured.child_transport
        || challenge.runner_token_context() != &measured.runner_token
        || challenge.child_image_context() != &measured.child_image
        || challenge.minimal_environment_context() != &measured.minimal_environment
        || challenge.control_server_identity_context() != &measured.control_server_identity
    {
        return Err(ChildHandshakeError::new(
            "child_handshake_peer_context_unexpected",
        ));
    }
    // Final-generation, exact job epoch, and start-contract values are
    // assertions by the held, independently authenticated authority process.
    // They are transcript-bound but are never manufactured by hashing fields
    // from this envelope. Local NULL-job membership, limits, and roster are
    // already bound into the parent-checked observed-launch context.
    Ok(AuthenticatedControlPeerWitness {
        role: inputs.role,
        challenge_digest: *challenge.challenge_digest(),
        _private: (),
    })
}

#[derive(Clone, Copy)]
enum BrokenPipeSemantics {
    Error,
    Eof,
}

struct BoundedOverlappedPipe {
    handle: HANDLE,
    deadline: Instant,
    broken_pipe: BrokenPipeSemantics,
}

impl BoundedOverlappedPipe {
    fn new(handle: HANDLE, deadline: Instant, broken_pipe: BrokenPipeSemantics) -> Self {
        Self {
            handle,
            deadline,
            broken_pipe,
        }
    }

    fn transfer(&mut self, bytes: Vec<u8>, direction: IoDirection) -> io::Result<IoCompletion> {
        overlapped_io(
            self.handle,
            bytes,
            direction,
            self.deadline,
            self.broken_pipe,
        )
    }
}

impl Read for BoundedOverlappedPipe {
    fn read(&mut self, target: &mut [u8]) -> io::Result<usize> {
        if target.is_empty() {
            return Ok(0);
        }
        let length = target.len().min(u32::MAX as usize);
        let completion = self.transfer(vec![0u8; length], IoDirection::Read)?;
        let transferred = completion.transferred as usize;
        if transferred > length {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "child pipe read exceeded buffer",
            ));
        }
        target[..transferred].copy_from_slice(&completion.buffer.as_slice()[..transferred]);
        Ok(transferred)
    }
}

impl Write for BoundedOverlappedPipe {
    fn write(&mut self, source: &[u8]) -> io::Result<usize> {
        if source.is_empty() {
            return Ok(0);
        }
        let length = source.len().min(u32::MAX as usize);
        let completion = self.transfer(source[..length].to_vec(), IoDirection::Write)?;
        let transferred = completion.transferred as usize;
        if transferred > length {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "child pipe write exceeded buffer",
            ));
        }
        Ok(transferred)
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

#[derive(Clone, Copy)]
enum IoDirection {
    Read,
    Write,
}

struct IoCompletion {
    transferred: u32,
    buffer: SensitiveIoBuffer,
}

fn overlapped_io(
    handle: HANDLE,
    buffer: Vec<u8>,
    direction: IoDirection,
    deadline: Instant,
    broken_pipe: BrokenPipeSemantics,
) -> io::Result<IoCompletion> {
    let mut operation = PendingOverlappedOperation::new(buffer)?;
    let length = u32::try_from(operation.buffer.len())
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "child pipe length invalid"))?;
    let mut transferred = 0u32;
    let started = unsafe {
        match direction {
            IoDirection::Read => ReadFile(
                handle,
                operation.buffer.as_mut_ptr(),
                length,
                &mut transferred,
                operation.overlapped.as_mut(),
            ),
            IoDirection::Write => WriteFile(
                handle,
                operation.buffer.as_ptr(),
                length,
                &mut transferred,
                operation.overlapped.as_mut(),
            ),
        }
    };
    if started != 0 {
        return Ok(IoCompletion {
            transferred,
            buffer: operation.buffer,
        });
    }
    match unsafe { GetLastError() } {
        ERROR_IO_PENDING => {}
        ERROR_BROKEN_PIPE if matches!(broken_pipe, BrokenPipeSemantics::Eof) => {
            return Ok(IoCompletion {
                transferred: 0,
                buffer: operation.buffer,
            })
        }
        ERROR_BROKEN_PIPE => {
            return Err(io::Error::new(
                io::ErrorKind::BrokenPipe,
                "child control pipe broken",
            ))
        }
        _ => {
            return Err(io::Error::new(
                io::ErrorKind::Other,
                "child pipe operation start failed",
            ))
        }
    }

    let remaining = deadline.saturating_duration_since(Instant::now());
    if remaining.is_zero() {
        return Err(cancelled_io_error(
            cancel_and_settle_or_quarantine(handle, operation),
            io::ErrorKind::TimedOut,
        ));
    }
    let wait_millis = remaining.as_millis().min(u128::from(u32::MAX - 1)).max(1) as u32;
    let wait = unsafe { WaitForSingleObject(operation.event.as_raw_handle().cast(), wait_millis) };
    if wait != WAIT_OBJECT_0 {
        return Err(cancelled_io_error(
            cancel_and_settle_or_quarantine(handle, operation),
            if wait == WAIT_TIMEOUT {
                io::ErrorKind::TimedOut
            } else {
                io::ErrorKind::Other
            },
        ));
    }

    transferred = 0;
    if unsafe { GetOverlappedResult(handle, operation.overlapped.as_mut(), &mut transferred, 0) }
        == 0
    {
        return match unsafe { GetLastError() } {
            ERROR_BROKEN_PIPE if matches!(broken_pipe, BrokenPipeSemantics::Eof) => {
                Ok(IoCompletion {
                    transferred: 0,
                    buffer: operation.buffer,
                })
            }
            ERROR_BROKEN_PIPE => Err(io::Error::new(
                io::ErrorKind::BrokenPipe,
                "child control pipe broken",
            )),
            ERROR_OPERATION_ABORTED => Err(io::Error::new(
                io::ErrorKind::Interrupted,
                "child pipe operation cancelled",
            )),
            _ => Err(io::Error::new(
                io::ErrorKind::Other,
                "child pipe operation completion failed",
            )),
        };
    }
    Ok(IoCompletion {
        transferred,
        buffer: operation.buffer,
    })
}

#[derive(Clone, Copy)]
enum CancelDisposition {
    Settled,
    Quarantined,
}

fn cancelled_io_error(disposition: CancelDisposition, settled_kind: io::ErrorKind) -> io::Error {
    match disposition {
        CancelDisposition::Settled => io::Error::new(settled_kind, "child pipe operation ended"),
        CancelDisposition::Quarantined => io::Error::new(
            io::ErrorKind::Other,
            "child pipe operation could not be safely settled",
        ),
    }
}

fn cancel_and_settle_or_quarantine(
    handle: HANDLE,
    mut operation: PendingOverlappedOperation,
) -> CancelDisposition {
    unsafe {
        CancelIoEx(handle, operation.overlapped.as_ref());
    }
    if unsafe { WaitForSingleObject(operation.event.as_raw_handle().cast(), CANCEL_SETTLE_MILLIS) }
        == WAIT_OBJECT_0
    {
        let mut transferred = 0u32;
        unsafe {
            GetOverlappedResult(handle, operation.overlapped.as_mut(), &mut transferred, 0);
        }
        CancelDisposition::Settled
    } else {
        std::mem::forget(operation);
        CancelDisposition::Quarantined
    }
}

struct PendingOverlappedOperation {
    event: OwnedHandle,
    overlapped: Box<OVERLAPPED>,
    buffer: SensitiveIoBuffer,
}

impl PendingOverlappedOperation {
    fn new(buffer: Vec<u8>) -> io::Result<Self> {
        let event = unsafe { CreateEventW(ptr::null(), 1, 0, ptr::null()) };
        if event.is_null() {
            return Err(io::Error::new(
                io::ErrorKind::Other,
                "child pipe event creation failed",
            ));
        }
        let event = unsafe { OwnedHandle::from_raw_handle(event as RawHandle) };
        let mut overlapped = Box::new(unsafe { zeroed::<OVERLAPPED>() });
        overlapped.hEvent = event.as_raw_handle().cast();
        Ok(Self {
            event,
            overlapped,
            buffer: SensitiveIoBuffer(buffer),
        })
    }
}

struct SensitiveIoBuffer(Vec<u8>);

impl SensitiveIoBuffer {
    fn len(&self) -> usize {
        self.0.len()
    }

    fn as_ptr(&self) -> *const u8 {
        self.0.as_ptr()
    }

    fn as_mut_ptr(&mut self) -> *mut u8 {
        self.0.as_mut_ptr()
    }

    fn as_slice(&self) -> &[u8] {
        &self.0
    }
}

impl Drop for SensitiveIoBuffer {
    fn drop(&mut self) {
        volatile_zero_bytes(&mut self.0);
    }
}

fn volatile_zero_bytes(values: &mut [u8]) {
    for value in values {
        unsafe { ptr::write_volatile(value, 0) };
    }
    compiler_fence(Ordering::SeqCst);
}

fn volatile_zero_wide(values: &mut [u16]) {
    for value in values {
        unsafe { ptr::write_volatile(value, 0) };
    }
    compiler_fence(Ordering::SeqCst);
}

struct SensitiveWordBuffer(Vec<usize>);

impl SensitiveWordBuffer {
    fn as_ptr(&self) -> *const usize {
        self.0.as_ptr()
    }
}

impl Drop for SensitiveWordBuffer {
    fn drop(&mut self) {
        for value in &mut self.0 {
            unsafe { ptr::write_volatile(value, 0) };
        }
        compiler_fence(Ordering::SeqCst);
    }
}

struct WindowsChildEvidence {
    contexts: MeasuredChildContexts,
    current_process: HeldProcessEvidence,
    current_thread: CurrentThreadObservation,
    control_server: HeldProcessEvidence,
    environment_digest: BootstrapDigest,
    job_observation_digest: BootstrapDigest,
}

impl WindowsChildEvidence {
    fn capture(transport: &ChildHandshakeTransport) -> Result<Self, ChildHandshakeError> {
        let current_process_id = unsafe { GetCurrentProcessId() };
        if current_process_id == 0 || current_process_id == transport.server_process_id() {
            return Err(ChildHandshakeError::new(
                "child_handshake_process_identity_invalid",
            ));
        }
        let expected_child_image = match transport.role() {
            ChildBootstrapRole::LifecycleDriver => DRIVER_IMAGE_NAME,
            ChildBootstrapRole::BridgeLauncher => BRIDGE_IMAGE_NAME,
        };
        let current_process = HeldProcessEvidence::open(
            current_process_id,
            expected_child_image,
            ProcessTokenPolicy::DedicatedRestrictedRunner,
        )?;
        let control_server = HeldProcessEvidence::open(
            transport.server_process_id(),
            AUTHORITY_IMAGE_NAME,
            ProcessTokenPolicy::RestrictedAuthority,
        )?;
        let environment_digest = current_environment_digest()?;
        let job_observation_digest = current_job_observation_digest(
            current_process.handle.as_raw_handle().cast(),
            current_process_id,
        )?;
        let current_thread = current_thread_observation()?;
        let child_transport_source = child_transport_observation_digest(transport)?;
        let control_server_source = control_server.observation_digest()?;
        let observed_launch_source = observed_launch_source_digest(
            transport.role(),
            current_process.process_id,
            current_process.creation_time,
            current_thread.thread_id,
            current_thread.creation_time,
            &child_transport_source,
            &current_process.token_digest,
            current_process.image.measurement_digest(),
            &environment_digest,
            &control_server_source,
            &job_observation_digest,
        );
        let contexts = MeasuredChildContexts {
            observed_launch: ChildObservedLaunchContextDigest::derive(&observed_launch_source)?,
            child_transport: ChildTransportContractContextDigest::derive(&child_transport_source)?,
            runner_token: RunnerTokenContextDigest::derive(&current_process.token_digest)?,
            child_image: ChildImageContextDigest::derive(
                current_process.image.measurement_digest(),
            )?,
            minimal_environment: MinimalEnvironmentContextDigest::derive(&environment_digest)?,
            control_server_identity: ControlServerIdentityContextDigest::derive(
                &control_server_source,
            )?,
        };
        let mut evidence = Self {
            contexts,
            current_process,
            current_thread,
            control_server,
            environment_digest,
            job_observation_digest,
        };
        evidence.revalidate()?;
        Ok(evidence)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct CurrentThreadObservation {
    thread_id: u32,
    creation_time: u64,
}

fn current_thread_observation() -> Result<CurrentThreadObservation, ChildHandshakeError> {
    let thread_id = unsafe { GetCurrentThreadId() };
    if thread_id == 0 {
        return Err(ChildHandshakeError::new(
            "child_handshake_current_thread_unavailable",
        ));
    }
    let raw = unsafe { OpenThread(THREAD_QUERY_LIMITED_INFORMATION, 0, thread_id) };
    if raw.is_null() || raw == INVALID_HANDLE_VALUE {
        return Err(ChildHandshakeError::new(
            "child_handshake_current_thread_unavailable",
        ));
    }
    let thread = unsafe { OwnedHandle::from_raw_handle(raw as RawHandle) };
    require_thread_without_impersonation_token(thread.as_raw_handle().cast())
        .map_err(|error| ChildHandshakeError::new(error.code()))?;
    let mut creation: FILETIME = unsafe { zeroed() };
    let mut exit: FILETIME = unsafe { zeroed() };
    let mut kernel: FILETIME = unsafe { zeroed() };
    let mut user: FILETIME = unsafe { zeroed() };
    if unsafe {
        GetThreadTimes(
            thread.as_raw_handle().cast(),
            &mut creation,
            &mut exit,
            &mut kernel,
            &mut user,
        )
    } == 0
    {
        return Err(ChildHandshakeError::new(
            "child_handshake_current_thread_epoch_unavailable",
        ));
    }
    let creation_time =
        (u64::from(creation.dwHighDateTime) << 32) | u64::from(creation.dwLowDateTime);
    if creation_time == 0 {
        return Err(ChildHandshakeError::new(
            "child_handshake_current_thread_epoch_invalid",
        ));
    }
    Ok(CurrentThreadObservation {
        thread_id,
        creation_time,
    })
}

impl ChildEvidenceProof for WindowsChildEvidence {
    fn contexts(&self) -> &MeasuredChildContexts {
        &self.contexts
    }

    fn revalidate(&mut self) -> Result<(), ChildHandshakeError> {
        self.current_process.revalidate()?;
        self.control_server.revalidate()?;
        if current_thread_observation()? != self.current_thread
            || current_environment_digest()? != self.environment_digest
            || current_job_observation_digest(
                self.current_process.handle.as_raw_handle().cast(),
                self.current_process.process_id,
            )? != self.job_observation_digest
        {
            return Err(ChildHandshakeError::new(
                "child_handshake_local_observation_changed",
            ));
        }
        Ok(())
    }
}

struct HeldProcessEvidence {
    handle: OwnedHandle,
    process_id: u32,
    creation_time: u64,
    token_digest: BootstrapDigest,
    image: HeldImageEvidence,
    token_validation: HeldTokenValidation,
}

// The parent-only branch is compiled into both child test crates because this
// file is shared by all three fixed binaries. Production child builds inherit
// the protocol module's existing dead-code policy; test builds need the narrow
// exemption until the service-only adapter calls the branch.
#[cfg_attr(test, allow(dead_code))]
enum HeldTokenValidation {
    Policy(ProcessTokenPolicy),
    ExpectedRunner(ExpectedRestrictedRunnerSid),
}

impl HeldTokenValidation {
    fn measure(&self, process: HANDLE) -> Result<BootstrapDigest, ChildHandshakeError> {
        match self {
            Self::Policy(policy) => measure_process_token_digest(process, *policy),
            Self::ExpectedRunner(expected) => {
                measure_expected_restricted_runner_token_digest(process, expected)
            }
        }
        .map_err(|error| ChildHandshakeError::new(error.code()))
    }
}

impl HeldProcessEvidence {
    fn open(
        process_id: u32,
        expected_image_name: &'static str,
        token_policy: ProcessTokenPolicy,
    ) -> Result<Self, ChildHandshakeError> {
        Self::open_with_token_validation(
            process_id,
            expected_image_name,
            HeldTokenValidation::Policy(token_policy),
        )
    }

    #[cfg_attr(test, allow(dead_code))]
    fn open_expected_runner(
        process_id: u32,
        expected_image_name: &'static str,
        expected_runner_sid: ExpectedRestrictedRunnerSid,
    ) -> Result<Self, ChildHandshakeError> {
        Self::open_with_token_validation(
            process_id,
            expected_image_name,
            HeldTokenValidation::ExpectedRunner(expected_runner_sid),
        )
    }

    fn open_with_token_validation(
        process_id: u32,
        expected_image_name: &'static str,
        token_validation: HeldTokenValidation,
    ) -> Result<Self, ChildHandshakeError> {
        if process_id == 0 {
            return Err(ChildHandshakeError::new(
                "child_handshake_process_identity_invalid",
            ));
        }
        let raw = unsafe {
            OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SYNCHRONIZE,
                0,
                process_id,
            )
        };
        // A restricted runner may be denied by the authority process DACL.
        // This path has no PID-only or weaker-observation fallback: production
        // provisioning must independently prove that this exact query access
        // is available before protected launch can be called ready.
        let handle = require_process_observation_handle(raw)?;
        let creation_time = process_creation_time(handle.as_raw_handle().cast())?;
        let token_digest = token_validation.measure(handle.as_raw_handle().cast())?;
        let image = HeldImageEvidence::open(handle.as_raw_handle().cast(), expected_image_name)?;
        let mut evidence = Self {
            handle,
            process_id,
            creation_time,
            token_digest,
            image,
            token_validation,
        };
        evidence.revalidate()?;
        Ok(evidence)
    }

    fn revalidate(&mut self) -> Result<(), ChildHandshakeError> {
        let handle = self.handle.as_raw_handle().cast();
        if unsafe { GetProcessId(handle) } != self.process_id
            || process_creation_time(handle)? != self.creation_time
            || self.token_validation.measure(handle)? != self.token_digest
        {
            return Err(ChildHandshakeError::new(
                "child_handshake_process_identity_changed",
            ));
        }
        require_process_still_running(handle)?;
        self.image.revalidate(handle)
    }

    fn observation_digest(&self) -> Result<BootstrapDigest, ChildHandshakeError> {
        let mut hasher = Sha256::new();
        hasher.update(CHILD_CONTROL_SERVER_OBSERVATION_DOMAIN);
        hasher.update(self.process_id.to_be_bytes());
        hasher.update(self.creation_time.to_be_bytes());
        hasher.update(self.token_digest);
        hasher.update(self.image.measurement_digest());
        let digest = hasher.finalize().into();
        require_nonzero_digest(digest, "child_handshake_server_observation_invalid")
    }
}

#[cfg_attr(test, allow(dead_code))]
pub(crate) struct ParentHeldRestrictedChildProcessEvidence {
    role: ChildBootstrapRole,
    process: HeldProcessEvidence,
}

impl fmt::Debug for ParentHeldRestrictedChildProcessEvidence {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ParentHeldRestrictedChildProcessEvidence")
            .field("role", &self.role)
            .field("process", &"<held-and-redacted>")
            .finish()
    }
}

#[cfg_attr(test, allow(dead_code))]
impl ParentHeldRestrictedChildProcessEvidence {
    pub(crate) fn capture(
        role: ChildBootstrapRole,
        process_id: u32,
        expected_runner_sid: ExpectedRestrictedRunnerSid,
    ) -> Result<Self, ChildHandshakeError> {
        let expected_image = match role {
            ChildBootstrapRole::LifecycleDriver => DRIVER_IMAGE_NAME,
            ChildBootstrapRole::BridgeLauncher => BRIDGE_IMAGE_NAME,
        };
        let process = HeldProcessEvidence::open_expected_runner(
            process_id,
            expected_image,
            expected_runner_sid,
        )?;
        Ok(Self { role, process })
    }

    pub(crate) const fn role(&self) -> ChildBootstrapRole {
        self.role
    }

    pub(crate) const fn process_id(&self) -> u32 {
        self.process.process_id
    }

    pub(crate) const fn process_creation_time(&self) -> u64 {
        self.process.creation_time
    }

    pub(crate) fn runner_token_digest(&self) -> &BootstrapDigest {
        &self.process.token_digest
    }

    pub(crate) fn image_measurement_digest(&self) -> &BootstrapDigest {
        self.process.image.measurement_digest()
    }

    pub(crate) fn image_content_digest(&self) -> &BootstrapDigest {
        self.process.image.content_digest()
    }

    pub(crate) fn process_image_file_for_exact_binding(&self) -> &File {
        &self.process.image.file
    }

    pub(crate) fn process_image_receipt_identity_digest(
        &self,
    ) -> Result<BootstrapDigest, ChildHandshakeError> {
        self.process.image.receipt_identity_digest()
    }

    pub(crate) fn revalidate(&mut self) -> Result<(), ChildHandshakeError> {
        self.process.revalidate()
    }
}

#[cfg_attr(test, allow(dead_code))]
pub(crate) struct ParentHeldAuthorityServerEvidence {
    process: HeldProcessEvidence,
    thread: CurrentThreadObservation,
}

impl fmt::Debug for ParentHeldAuthorityServerEvidence {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("ParentHeldAuthorityServerEvidence(<held-and-redacted>)")
    }
}

#[cfg_attr(test, allow(dead_code))]
impl ParentHeldAuthorityServerEvidence {
    pub(crate) fn capture_current() -> Result<Self, ChildHandshakeError> {
        let process_id = unsafe { GetCurrentProcessId() };
        let thread = current_thread_observation()?;
        let process = HeldProcessEvidence::open(
            process_id,
            AUTHORITY_IMAGE_NAME,
            ProcessTokenPolicy::RestrictedAuthority,
        )?;
        Ok(Self { process, thread })
    }

    pub(crate) const fn process_id(&self) -> u32 {
        self.process.process_id
    }

    pub(crate) const fn process_creation_time(&self) -> u64 {
        self.process.creation_time
    }

    pub(crate) fn observation_digest(&self) -> Result<BootstrapDigest, ChildHandshakeError> {
        self.process.observation_digest()
    }

    pub(crate) fn revalidate(&mut self) -> Result<(), ChildHandshakeError> {
        self.process.revalidate()?;
        if current_thread_observation()? != self.thread {
            return Err(ChildHandshakeError::new(
                "child_handshake_authority_thread_changed",
            ));
        }
        Ok(())
    }
}

/// `STILL_ACTIVE` is an exit-code sentinel, not a liveness proof: Windows also
/// permits a process to terminate with that numeric exit code. The wait state
/// of the held synchronizable process handle is therefore authoritative.
fn require_process_still_running(process: HANDLE) -> Result<(), ChildHandshakeError> {
    let mut exit_code = 0u32;
    if unsafe { GetExitCodeProcess(process, &mut exit_code) } == 0
        || exit_code != STILL_ACTIVE as u32
        || unsafe { WaitForSingleObject(process, 0) } != WAIT_TIMEOUT
    {
        return Err(ChildHandshakeError::new(
            "child_handshake_process_not_running",
        ));
    }
    Ok(())
}

fn require_process_observation_handle(raw: HANDLE) -> Result<OwnedHandle, ChildHandshakeError> {
    if raw.is_null() || raw == INVALID_HANDLE_VALUE {
        return Err(ChildHandshakeError::new(
            "child_handshake_process_open_failed",
        ));
    }
    Ok(unsafe { OwnedHandle::from_raw_handle(raw as RawHandle) })
}

#[derive(Clone, Copy, PartialEq, Eq)]
struct ImageIdentity {
    volume_serial: u64,
    file_id: [u8; 16],
    byte_length: u64,
    link_count: u32,
    attributes: u32,
}

struct HeldImageEvidence {
    file: File,
    path: PathBuf,
    identity: ImageIdentity,
    sha256: BootstrapDigest,
    measurement_digest: BootstrapDigest,
}

impl HeldImageEvidence {
    fn open(process: HANDLE, expected_name: &'static str) -> Result<Self, ChildHandshakeError> {
        let path = process_image_path(process)?;
        if !path
            .file_name()
            .and_then(OsStr::to_str)
            .is_some_and(|value| value.eq_ignore_ascii_case(expected_name))
        {
            return Err(ChildHandshakeError::new(
                "child_handshake_process_image_name_unexpected",
            ));
        }
        let metadata = std::fs::symlink_metadata(&path)
            .map_err(|_| ChildHandshakeError::new("child_handshake_image_metadata_failed"))?;
        if !metadata.is_file()
            || metadata.file_type().is_symlink()
            || metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
            || metadata.len() == 0
            || metadata.len() > MAX_IMAGE_BYTES
        {
            return Err(ChildHandshakeError::new(
                "child_handshake_image_metadata_invalid",
            ));
        }
        let mut file = OpenOptions::new()
            .read(true)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
            .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_SEQUENTIAL_SCAN)
            .open(&path)
            .map_err(|_| ChildHandshakeError::new("child_handshake_image_open_failed"))?;
        require_exact_handle_path(&file, &path)?;
        let (identity, sha256) = read_image_measurement(&mut file)?;
        if identity.link_count != 1
            || identity.attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT) != 0
            || identity.byte_length != metadata.len()
        {
            return Err(ChildHandshakeError::new(
                "child_handshake_image_identity_invalid",
            ));
        }
        let measurement_digest = image_measurement_digest(&path, &identity, &sha256)?;
        Ok(Self {
            file,
            path,
            identity,
            sha256,
            measurement_digest,
        })
    }

    fn revalidate(&mut self, process: HANDLE) -> Result<(), ChildHandshakeError> {
        if !paths_equal(&process_image_path(process)?, &self.path) {
            return Err(ChildHandshakeError::new(
                "child_handshake_image_path_changed",
            ));
        }
        require_exact_handle_path(&self.file, &self.path)?;
        let (identity, sha256) = read_image_measurement(&mut self.file)?;
        if identity != self.identity || sha256 != self.sha256 {
            return Err(ChildHandshakeError::new(
                "child_handshake_image_identity_changed",
            ));
        }
        Ok(())
    }

    fn measurement_digest(&self) -> &BootstrapDigest {
        &self.measurement_digest
    }

    fn receipt_identity_digest(&self) -> Result<BootstrapDigest, ChildHandshakeError> {
        let mut hasher = Sha256::new();
        hasher.update(PARENT_PROCESS_IMAGE_RECEIPT_IDENTITY_DOMAIN);
        hasher.update(self.measurement_digest);
        hasher.update(self.identity.volume_serial.to_be_bytes());
        hasher.update(self.identity.file_id);
        hasher.update(self.identity.byte_length.to_be_bytes());
        hasher.update(self.identity.link_count.to_be_bytes());
        hasher.update(self.identity.attributes.to_be_bytes());
        require_nonzero_digest(
            hasher.finalize().into(),
            "child_handshake_process_image_receipt_identity_invalid",
        )
    }

    #[cfg_attr(test, allow(dead_code))]
    fn content_digest(&self) -> &BootstrapDigest {
        &self.sha256
    }
}

fn process_creation_time(process: HANDLE) -> Result<u64, ChildHandshakeError> {
    let mut creation: FILETIME = unsafe { zeroed() };
    let mut exit: FILETIME = unsafe { zeroed() };
    let mut kernel: FILETIME = unsafe { zeroed() };
    let mut user: FILETIME = unsafe { zeroed() };
    if unsafe { GetProcessTimes(process, &mut creation, &mut exit, &mut kernel, &mut user) } == 0 {
        return Err(ChildHandshakeError::new(
            "child_handshake_process_epoch_unavailable",
        ));
    }
    let value = (u64::from(creation.dwHighDateTime) << 32) | u64::from(creation.dwLowDateTime);
    if value == 0 {
        return Err(ChildHandshakeError::new(
            "child_handshake_process_epoch_invalid",
        ));
    }
    Ok(value)
}

fn process_image_path(process: HANDLE) -> Result<PathBuf, ChildHandshakeError> {
    let mut words = SensitiveWideBuffer(vec![0u16; 32_768]);
    let mut length = words.0.len() as u32;
    if unsafe { QueryFullProcessImageNameW(process, 0, words.0.as_mut_ptr(), &mut length) } == 0
        || length == 0
        || length as usize >= words.0.len()
    {
        return Err(ChildHandshakeError::new(
            "child_handshake_image_path_unavailable",
        ));
    }
    words.0.truncate(length as usize);
    if words.0.contains(&0) {
        return Err(ChildHandshakeError::new(
            "child_handshake_image_path_invalid",
        ));
    }
    Ok(PathBuf::from(OsString::from_wide(&words.0)))
}

fn read_image_measurement(
    file: &mut File,
) -> Result<(ImageIdentity, BootstrapDigest), ChildHandshakeError> {
    let before = image_identity(file)?;
    if before.byte_length == 0 || before.byte_length > MAX_IMAGE_BYTES {
        return Err(ChildHandshakeError::new(
            "child_handshake_image_size_invalid",
        ));
    }
    file.seek(SeekFrom::Start(0))
        .map_err(|_| ChildHandshakeError::new("child_handshake_image_read_failed"))?;
    let mut hasher = Sha256::new();
    let mut buffer = [0u8; 64 * 1024];
    let mut length = 0u64;
    loop {
        let count = file
            .read(&mut buffer)
            .map_err(|_| ChildHandshakeError::new("child_handshake_image_read_failed"))?;
        if count == 0 {
            break;
        }
        length = length
            .checked_add(count as u64)
            .ok_or_else(|| ChildHandshakeError::new("child_handshake_image_size_invalid"))?;
        if length > MAX_IMAGE_BYTES {
            volatile_zero_bytes(&mut buffer);
            return Err(ChildHandshakeError::new(
                "child_handshake_image_size_invalid",
            ));
        }
        hasher.update(&buffer[..count]);
        volatile_zero_bytes(&mut buffer[..count]);
    }
    file.seek(SeekFrom::Start(0))
        .map_err(|_| ChildHandshakeError::new("child_handshake_image_read_failed"))?;
    let after = image_identity(file)?;
    if before != after || length != before.byte_length {
        return Err(ChildHandshakeError::new(
            "child_handshake_image_identity_changed",
        ));
    }
    Ok((before, hasher.finalize().into()))
}

fn image_identity(file: &File) -> Result<ImageIdentity, ChildHandshakeError> {
    let mut information: BY_HANDLE_FILE_INFORMATION = unsafe { zeroed() };
    if unsafe { GetFileInformationByHandle(file.as_raw_handle().cast(), &mut information) } == 0 {
        return Err(ChildHandshakeError::new(
            "child_handshake_image_identity_unavailable",
        ));
    }
    let volume_serial = u64::from(information.dwVolumeSerialNumber);
    let mut file_id = [0u8; 16];
    file_id[..8].copy_from_slice(
        &((u64::from(information.nFileIndexHigh) << 32) | u64::from(information.nFileIndexLow))
            .to_be_bytes(),
    );
    let byte_length =
        (u64::from(information.nFileSizeHigh) << 32) | u64::from(information.nFileSizeLow);
    if volume_serial == 0
        || file_id.iter().all(|byte| *byte == 0)
        || byte_length == 0
        || information.nNumberOfLinks == 0
    {
        return Err(ChildHandshakeError::new(
            "child_handshake_image_identity_invalid",
        ));
    }
    Ok(ImageIdentity {
        volume_serial,
        file_id,
        byte_length,
        link_count: information.nNumberOfLinks,
        attributes: information.dwFileAttributes,
    })
}

fn image_measurement_digest(
    path: &Path,
    identity: &ImageIdentity,
    sha256: &BootstrapDigest,
) -> Result<BootstrapDigest, ChildHandshakeError> {
    let path_words = SensitiveWideBuffer(path.as_os_str().encode_wide().collect::<Vec<_>>());
    if path_words.0.is_empty() || path_words.0.len() > 32_767 {
        return Err(ChildHandshakeError::new(
            "child_handshake_image_path_invalid",
        ));
    }
    let mut hasher = Sha256::new();
    hasher.update(CHILD_PROCESS_IMAGE_OBSERVATION_DOMAIN);
    hasher.update((path_words.0.len() as u32).to_be_bytes());
    for value in &path_words.0 {
        hasher.update(value.to_le_bytes());
    }
    hasher.update(identity.volume_serial.to_be_bytes());
    hasher.update(identity.file_id);
    hasher.update(identity.byte_length.to_be_bytes());
    hasher.update(identity.link_count.to_be_bytes());
    hasher.update(identity.attributes.to_be_bytes());
    hasher.update(sha256);
    require_nonzero_digest(
        hasher.finalize().into(),
        "child_handshake_image_observation_invalid",
    )
}

fn require_exact_handle_path(file: &File, expected: &Path) -> Result<(), ChildHandshakeError> {
    let mut words = SensitiveWideBuffer(vec![0u16; 32_768]);
    let length = unsafe {
        GetFinalPathNameByHandleW(
            file.as_raw_handle().cast(),
            words.0.as_mut_ptr(),
            words.0.len() as u32,
            0,
        )
    } as usize;
    if length == 0 || length >= words.0.len() {
        return Err(ChildHandshakeError::new(
            "child_handshake_image_handle_path_unavailable",
        ));
    }
    words.0.truncate(length);
    if words.0.contains(&0) {
        return Err(ChildHandshakeError::new(
            "child_handshake_image_handle_path_invalid",
        ));
    }
    let actual = OsString::from_wide(&words.0).to_string_lossy().into_owned();
    let actual = actual
        .strip_prefix(r"\\?\UNC\")
        .map(|value| format!(r"\\{value}"))
        .or_else(|| actual.strip_prefix(r"\\?\").map(str::to_string))
        .unwrap_or(actual);
    if !actual.eq_ignore_ascii_case(expected.to_string_lossy().as_ref()) {
        return Err(ChildHandshakeError::new(
            "child_handshake_image_handle_path_mismatch",
        ));
    }
    Ok(())
}

fn paths_equal(left: &Path, right: &Path) -> bool {
    left.to_string_lossy()
        .eq_ignore_ascii_case(&right.to_string_lossy())
}

fn current_environment_digest() -> Result<BootstrapDigest, ChildHandshakeError> {
    let mut entries = BTreeMap::<String, OsString>::new();
    for (name, value) in std::env::vars_os() {
        let name = name
            .into_string()
            .map_err(|_| ChildHandshakeError::new("child_handshake_environment_shape_invalid"))?;
        let canonical_name = name.to_ascii_uppercase();
        if !FIXED_ENVIRONMENT_NAMES.contains(&canonical_name.as_str())
            || entries.insert(canonical_name, value).is_some()
        {
            return Err(ChildHandshakeError::new(
                "child_handshake_environment_shape_invalid",
            ));
        }
    }
    if entries.len() != FIXED_ENVIRONMENT_NAMES.len()
        || !FIXED_ENVIRONMENT_NAMES
            .iter()
            .all(|name| entries.contains_key(*name))
    {
        return Err(ChildHandshakeError::new(
            "child_handshake_environment_shape_invalid",
        ));
    }
    let mut block = SensitiveWideBuffer(Vec::new());
    for (name, value) in entries {
        block.0.extend(OsStr::new(&name).encode_wide());
        block.0.push(u16::from(b'='));
        block.0.extend(value.encode_wide());
        block.0.push(0);
    }
    block.0.push(0);
    if block.0.len() <= 2 || block.0.len() > MAX_ENVIRONMENT_UTF16_UNITS {
        return Err(ChildHandshakeError::new(
            "child_handshake_environment_size_invalid",
        ));
    }
    environment_observation_digest_from_utf16(&block.0)
}

pub(crate) fn environment_observation_digest_from_utf16(
    block: &[u16],
) -> Result<BootstrapDigest, ChildHandshakeError> {
    if block.len() <= 2
        || block.len() > MAX_ENVIRONMENT_UTF16_UNITS
        || block.last() != Some(&0)
        || block.get(block.len().saturating_sub(2)) != Some(&0)
    {
        return Err(ChildHandshakeError::new(
            "child_handshake_environment_size_invalid",
        ));
    }
    let mut hasher = Sha256::new();
    hasher.update(CHILD_ENVIRONMENT_BLOCK_OBSERVATION_DOMAIN);
    hasher.update((block.len() as u64).to_be_bytes());
    for value in block {
        hasher.update(value.to_le_bytes());
    }
    require_nonzero_digest(
        hasher.finalize().into(),
        "child_handshake_environment_observation_invalid",
    )
}

struct SensitiveWideBuffer(Vec<u16>);

impl Drop for SensitiveWideBuffer {
    fn drop(&mut self) {
        volatile_zero_wide(&mut self.0);
    }
}

fn current_job_observation_digest(
    process: HANDLE,
    current_process_id: u32,
) -> Result<BootstrapDigest, ChildHandshakeError> {
    let mut in_job = 0;
    if unsafe { IsProcessInJob(process, null_mut(), &mut in_job) } == 0 || in_job == 0 {
        return Err(ChildHandshakeError::new(
            "child_handshake_job_membership_required",
        ));
    }

    let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { zeroed() };
    let mut returned = 0u32;
    if unsafe {
        QueryInformationJobObject(
            null_mut(),
            JobObjectExtendedLimitInformation,
            (&mut limits as *mut JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
            size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            &mut returned,
        )
    } == 0
        || returned as usize != size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>()
    {
        return Err(ChildHandshakeError::new(
            "child_handshake_job_limit_readback_failed",
        ));
    }
    let limit_flags = limits.BasicLimitInformation.LimitFlags;
    if limit_flags & JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE == 0
        || limit_flags & (JOB_OBJECT_LIMIT_BREAKAWAY_OK | JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK) != 0
    {
        return Err(ChildHandshakeError::new(
            "child_handshake_job_limit_invalid",
        ));
    }

    let mut accounting: JOBOBJECT_BASIC_ACCOUNTING_INFORMATION = unsafe { zeroed() };
    returned = 0;
    if unsafe {
        QueryInformationJobObject(
            null_mut(),
            JobObjectBasicAccountingInformation,
            (&mut accounting as *mut JOBOBJECT_BASIC_ACCOUNTING_INFORMATION).cast(),
            size_of::<JOBOBJECT_BASIC_ACCOUNTING_INFORMATION>() as u32,
            &mut returned,
        )
    } == 0
        || returned as usize != size_of::<JOBOBJECT_BASIC_ACCOUNTING_INFORMATION>()
    {
        return Err(ChildHandshakeError::new(
            "child_handshake_job_accounting_readback_failed",
        ));
    }

    let roster_header = size_of::<u32>() * 2;
    let roster_bytes = size_of::<JOBOBJECT_BASIC_PROCESS_ID_LIST>()
        .checked_add((MAX_JOB_ROSTER_PROCESSES - 1) * size_of::<usize>())
        .ok_or_else(|| ChildHandshakeError::new("child_handshake_job_roster_invalid"))?;
    let word_count = roster_bytes
        .checked_add(size_of::<usize>() - 1)
        .ok_or_else(|| ChildHandshakeError::new("child_handshake_job_roster_invalid"))?
        / size_of::<usize>();
    let mut roster_buffer = SensitiveWordBuffer(vec![0usize; word_count]);
    returned = 0;
    if unsafe {
        QueryInformationJobObject(
            null_mut(),
            JobObjectBasicProcessIdList,
            roster_buffer.0.as_mut_ptr().cast(),
            roster_bytes as u32,
            &mut returned,
        )
    } == 0
        || returned < roster_header as u32
        || returned as usize > roster_bytes
    {
        return Err(ChildHandshakeError::new(
            "child_handshake_job_roster_readback_failed",
        ));
    }
    let list = unsafe {
        &*(roster_buffer
            .as_ptr()
            .cast::<JOBOBJECT_BASIC_PROCESS_ID_LIST>())
    };
    let count = list.NumberOfProcessIdsInList as usize;
    let assigned = list.NumberOfAssignedProcesses as usize;
    let expected_returned = roster_header
        .checked_add(
            count
                .checked_mul(size_of::<usize>())
                .ok_or_else(|| ChildHandshakeError::new("child_handshake_job_roster_invalid"))?,
        )
        .ok_or_else(|| ChildHandshakeError::new("child_handshake_job_roster_invalid"))?;
    if count == 0
        || count > MAX_JOB_ROSTER_PROCESSES
        || assigned != count
        || (returned as usize) < expected_returned
    {
        return Err(ChildHandshakeError::new(
            "child_handshake_job_roster_invalid",
        ));
    }
    let process_ids = unsafe { std::slice::from_raw_parts(list.ProcessIdList.as_ptr(), count) };
    let mut roster = BTreeSet::new();
    for process_id in process_ids {
        let process_id = u32::try_from(*process_id)
            .map_err(|_| ChildHandshakeError::new("child_handshake_job_roster_invalid"))?;
        if process_id == 0 || !roster.insert(process_id) {
            return Err(ChildHandshakeError::new(
                "child_handshake_job_roster_invalid",
            ));
        }
    }
    if !roster.contains(&current_process_id)
        || accounting.ActiveProcesses as usize != roster.len()
        || accounting.TotalProcesses < accounting.ActiveProcesses
    {
        return Err(ChildHandshakeError::new(
            "child_handshake_job_membership_unexpected",
        ));
    }

    job_observation_digest_from_parts(
        limit_flags,
        limits.BasicLimitInformation.ActiveProcessLimit,
        accounting.TotalProcesses,
        accounting.ActiveProcesses,
        accounting.TotalTerminatedProcesses,
        &roster,
    )
}

pub(crate) fn job_observation_digest_from_parts(
    limit_flags: u32,
    active_process_limit: u32,
    total_processes: u32,
    active_processes: u32,
    total_terminated_processes: u32,
    roster: &BTreeSet<u32>,
) -> Result<BootstrapDigest, ChildHandshakeError> {
    if roster.is_empty()
        || roster.len() > MAX_JOB_ROSTER_PROCESSES
        || roster.iter().any(|process_id| *process_id == 0)
        || active_processes as usize != roster.len()
        || total_processes < active_processes
    {
        return Err(ChildHandshakeError::new(
            "child_handshake_job_observation_invalid",
        ));
    }
    let mut hasher = Sha256::new();
    hasher.update(CHILD_JOB_OBSERVATION_DOMAIN);
    hasher.update(limit_flags.to_be_bytes());
    hasher.update(active_process_limit.to_be_bytes());
    hasher.update(total_processes.to_be_bytes());
    hasher.update(active_processes.to_be_bytes());
    hasher.update(total_terminated_processes.to_be_bytes());
    hasher.update((roster.len() as u32).to_be_bytes());
    for process_id in roster {
        hasher.update(process_id.to_be_bytes());
    }
    require_nonzero_digest(
        hasher.finalize().into(),
        "child_handshake_job_observation_invalid",
    )
}

fn child_transport_observation_digest(
    transport: &ChildHandshakeTransport,
) -> Result<BootstrapDigest, ChildHandshakeError> {
    child_transport_observation_digest_from_parts(
        transport.role(),
        transport.server_process_id(),
        transport.role_capability_set(),
        transport.raw_handle_list_digest(),
    )
}

pub(crate) fn child_transport_observation_digest_from_parts(
    role: ChildBootstrapRole,
    server_process_id: u32,
    role_capability_set: &crate::primitive_evidence_child_protocol::RoleCapabilitySetBinding,
    raw_handle_list_digest: crate::primitive_evidence_child_protocol::RoleRawHandleListDigest,
) -> Result<BootstrapDigest, ChildHandshakeError> {
    if server_process_id == 0
        || role_capability_set.role() != role
        || raw_handle_list_digest.role() != role
        || role_capability_set.raw_handle_list_digest() != raw_handle_list_digest.as_bytes()
    {
        return Err(ChildHandshakeError::new(
            "child_handshake_transport_observation_invalid",
        ));
    }
    let mut hasher = Sha256::new();
    hasher.update(CHILD_TRANSPORT_OBSERVATION_DOMAIN);
    hasher.update([role.wire_value()]);
    hasher.update(server_process_id.to_be_bytes());
    hasher.update(role_capability_set.semantic_digest());
    hasher.update(raw_handle_list_digest.as_bytes());
    require_nonzero_digest(
        hasher.finalize().into(),
        "child_handshake_transport_observation_invalid",
    )
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn observed_launch_source_digest(
    role: ChildBootstrapRole,
    process_id: u32,
    process_creation_time: u64,
    primary_thread_id: u32,
    primary_thread_creation_time: u64,
    child_transport: &BootstrapDigest,
    runner_token: &BootstrapDigest,
    child_image: &BootstrapDigest,
    minimal_environment: &BootstrapDigest,
    control_server: &BootstrapDigest,
    job_observation: &BootstrapDigest,
) -> BootstrapDigest {
    let mut hasher = Sha256::new();
    hasher.update(CHILD_OBSERVED_LAUNCH_SOURCE_DOMAIN);
    hasher.update([role.wire_value()]);
    hasher.update(process_id.to_be_bytes());
    hasher.update(process_creation_time.to_be_bytes());
    hasher.update(primary_thread_id.to_be_bytes());
    hasher.update(primary_thread_creation_time.to_be_bytes());
    for digest in [
        child_transport,
        runner_token,
        child_image,
        minimal_environment,
        control_server,
        job_observation,
    ] {
        hasher.update(digest);
    }
    hasher.finalize().into()
}

fn require_nonzero_digest(
    digest: BootstrapDigest,
    error_code: &'static str,
) -> Result<BootstrapDigest, ChildHandshakeError> {
    if digest.iter().all(|byte| *byte == 0) {
        Err(ChildHandshakeError::new(error_code))
    } else {
        Ok(digest)
    }
}

#[cfg(test)]
mod tests {
    use super::super as protocol;
    use super::*;
    use std::{
        cell::RefCell, io::Cursor, os::windows::process::CommandExt, process::Command, rc::Rc,
    };
    use windows_sys::Win32::System::Threading::CREATE_NO_WINDOW;

    fn inputs(role: ChildBootstrapRole) -> HandshakeInputs {
        let slots = protocol::child_role_capability_schema(role)
            .iter()
            .enumerate()
            .map(|(index, descriptor)| {
                protocol::ChildRoleCapabilitySlotBinding::new(
                    descriptor.semantic(),
                    [0x61 + index as u8; 32],
                    0x101usize * (index + 1),
                )
            })
            .collect::<Vec<_>>();
        let role_capability_set =
            protocol::RoleCapabilitySetBinding::derive_for_test(role, &slots).unwrap();
        let raw_handles = std::array::from_fn(|index| 0x101usize * (index + 1));
        let raw_handle_list_digest =
            protocol::RoleRawHandleListDigest::derive(role, &raw_handles).unwrap();
        HandshakeInputs {
            role,
            role_capability_set,
            raw_handle_list_digest,
        }
    }

    fn contexts(seed: u8) -> MeasuredChildContexts {
        MeasuredChildContexts {
            observed_launch: ChildObservedLaunchContextDigest::derive(&[seed; 32]).unwrap(),
            child_transport: ChildTransportContractContextDigest::derive(
                &[seed.wrapping_add(1); 32],
            )
            .unwrap(),
            runner_token: RunnerTokenContextDigest::derive(&[seed.wrapping_add(2); 32]).unwrap(),
            child_image: ChildImageContextDigest::derive(&[seed.wrapping_add(3); 32]).unwrap(),
            minimal_environment: MinimalEnvironmentContextDigest::derive(
                &[seed.wrapping_add(4); 32],
            )
            .unwrap(),
            control_server_identity: ControlServerIdentityContextDigest::derive(
                &[seed.wrapping_add(5); 32],
            )
            .unwrap(),
        }
    }

    struct FakeEvidence {
        contexts: MeasuredChildContexts,
        validation_calls: usize,
        fail_on_call: Option<(usize, &'static str)>,
    }

    impl ChildEvidenceProof for FakeEvidence {
        fn contexts(&self) -> &MeasuredChildContexts {
            &self.contexts
        }

        fn revalidate(&mut self) -> Result<(), ChildHandshakeError> {
            self.validation_calls += 1;
            if let Some((call, code)) = self.fail_on_call {
                if self.validation_calls == call {
                    return Err(ChildHandshakeError::new(code));
                }
            }
            Ok(())
        }
    }

    #[derive(Clone, Copy)]
    enum ParentContextMutation {
        None,
        RunnerToken,
        ControlServer,
    }

    #[derive(Clone, Copy)]
    enum BootstrapMode {
        ExactEof,
        Short,
        Overlong,
        NoEof,
        Broken,
    }

    #[derive(Clone, Copy)]
    enum ControlFault {
        None,
        ReadyTimeout,
        ExpectationBroken,
    }

    struct FixtureState {
        frame: Option<protocol::ParentPreparedChildBootstrapFrame>,
        expectations: protocol::ParentChildBootstrapExpectations,
        ready: Vec<u8>,
        expectation: Vec<u8>,
        bootstrap: Vec<u8>,
        ack: Vec<u8>,
        expectation_offset: usize,
        bootstrap_offset: usize,
        response_prepared: bool,
        awaiting_ack: Option<protocol::ParentAwaitingBootstrapAck>,
        expectation_sent: Option<protocol::ParentExpectationSent>,
        bootstrap_mode: BootstrapMode,
        control_fault: ControlFault,
        forced_expectation: Option<Vec<u8>>,
    }

    impl FixtureState {
        fn new(
            inputs: &HandshakeInputs,
            measured: MeasuredChildContexts,
            mutation: ParentContextMutation,
            bootstrap_mode: BootstrapMode,
            control_fault: ControlFault,
            forced_expectation: Option<Vec<u8>>,
        ) -> Self {
            let authority = protocol::AuthorityBindingDigest::derive(&[0x11; 32]).unwrap();
            let ticket = protocol::TicketBindingDigest::derive(&[0x22; 32]).unwrap();
            let run = protocol::RunBindingDigest::derive(&[0x33; 32]).unwrap();
            let policy = protocol::PolicyBindingDigest::derive(&[0x44; 32]).unwrap();
            let capabilities = std::array::from_fn(|index| [0x50 + index as u8; 32]);
            let global = protocol::GlobalCapabilitySetDigest::derive(&capabilities).unwrap();
            let mut secret = [0x77; 32];
            let private_control =
                protocol::PrivateControlCapability::take_for_parent(&mut secret).unwrap();
            assert!(secret.iter().all(|byte| *byte == 0));
            let commitment = protocol::PrivateControlCapabilityCommitment::from_parent_capability(
                &private_control,
            )
            .unwrap();
            let bindings = protocol::ChildBootstrapBindings::prepare_for_parent(
                authority,
                ticket,
                run,
                policy,
                global,
                inputs.role_capability_set.clone(),
                private_control,
            )
            .unwrap();
            let frame = protocol::ParentPreparedChildBootstrapFrame::prepare(inputs.role, bindings)
                .unwrap();

            let runner_token = match mutation {
                ParentContextMutation::RunnerToken => {
                    RunnerTokenContextDigest::derive(&[0xE1; 32]).unwrap()
                }
                _ => measured.runner_token,
            };
            let control_server_identity = match mutation {
                ParentContextMutation::ControlServer => {
                    ControlServerIdentityContextDigest::derive(&[0xE2; 32]).unwrap()
                }
                _ => measured.control_server_identity,
            };
            let execution =
                protocol::AuthorityChildExecutionContext::from_independent_measurements(
                    protocol::FinalGenerationContextDigest::derive(&[0x91; 32]).unwrap(),
                    measured.child_transport,
                    protocol::StartContractContextDigest::derive(&[0x93; 32]).unwrap(),
                    protocol::JobMembershipEpochContextDigest::derive(&[0x94; 32]).unwrap(),
                    runner_token,
                    measured.child_image,
                    measured.minimal_environment,
                    control_server_identity,
                )
                .unwrap();
            let expectations =
                protocol::ParentChildBootstrapExpectations::from_authority_projection(
                    inputs.role,
                    protocol::AuthorityBindingDigest::derive(&[0x11; 32]).unwrap(),
                    protocol::TicketBindingDigest::derive(&[0x22; 32]).unwrap(),
                    protocol::RunBindingDigest::derive(&[0x33; 32]).unwrap(),
                    protocol::PolicyBindingDigest::derive(&[0x44; 32]).unwrap(),
                    protocol::GlobalCapabilitySetDigest::derive(&capabilities).unwrap(),
                    inputs.role_capability_set.clone(),
                    commitment,
                    measured.observed_launch,
                    execution,
                )
                .unwrap();
            Self {
                frame: Some(frame),
                expectations,
                ready: Vec::new(),
                expectation: Vec::new(),
                bootstrap: Vec::new(),
                ack: Vec::new(),
                expectation_offset: 0,
                bootstrap_offset: 0,
                response_prepared: false,
                awaiting_ack: None,
                expectation_sent: None,
                bootstrap_mode,
                control_fault,
                forced_expectation,
            }
        }

        fn prepare_response(&mut self) -> Result<(), protocol::ChildProtocolError> {
            if self.response_prepared {
                return Ok(());
            }
            self.response_prepared = true;
            if let Some(forced) = self.forced_expectation.take() {
                self.expectation = forced;
                return Ok(());
            }
            let ready = protocol::ReceivedChildReady::read_from(&mut Cursor::new(&self.ready))?;
            let frame = self.frame.take().expect("single parent frame");
            let frame_binding = *frame.frame_binding_digest();
            let envelope = protocol::PreparedExpectationEnvelope::prepare(
                &ready,
                protocol::AuthorityHandshakeNonce::from_fresh_bytes([0xD1; 32])?,
                &frame_binding,
                &self.expectations,
            )?;
            self.expectation_sent = Some(envelope.write_to(&mut self.expectation)?);
            self.awaiting_ack = Some(frame.write_complete_to(&mut self.bootstrap)?);
            Ok(())
        }

        fn verify_ack(&mut self) -> Result<(), protocol::ChildProtocolError> {
            let received = protocol::ReceivedBootstrapAck::read_from(&mut Cursor::new(&self.ack))?;
            self.awaiting_ack
                .take()
                .expect("parent awaits ack")
                .verify_ack(
                    self.expectation_sent.take().expect("expectation was sent"),
                    received,
                )
        }
    }

    struct FixtureControl(Rc<RefCell<FixtureState>>);

    impl Write for FixtureControl {
        fn write(&mut self, source: &[u8]) -> io::Result<usize> {
            let mut state = self.0.borrow_mut();
            if state.ready.is_empty() && matches!(state.control_fault, ControlFault::ReadyTimeout) {
                return Err(io::Error::new(io::ErrorKind::TimedOut, "fixture timeout"));
            }
            let written = source.len().min(7);
            if state.ready.len() < protocol::CHILD_READY_MESSAGE_LEN {
                let remaining = protocol::CHILD_READY_MESSAGE_LEN - state.ready.len();
                let count = written.min(remaining);
                state.ready.extend_from_slice(&source[..count]);
                if state.ready.len() == protocol::CHILD_READY_MESSAGE_LEN {
                    state
                        .prepare_response()
                        .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "fixture setup"))?;
                }
                Ok(count)
            } else {
                state.ack.extend_from_slice(&source[..written]);
                Ok(written)
            }
        }

        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }

    impl Read for FixtureControl {
        fn read(&mut self, target: &mut [u8]) -> io::Result<usize> {
            let mut state = self.0.borrow_mut();
            if matches!(state.control_fault, ControlFault::ExpectationBroken) {
                return Err(io::Error::new(io::ErrorKind::BrokenPipe, "fixture broken"));
            }
            let remaining = state
                .expectation
                .len()
                .saturating_sub(state.expectation_offset);
            if remaining == 0 {
                return Ok(0);
            }
            let count = remaining.min(target.len()).min(13);
            target[..count].copy_from_slice(
                &state.expectation[state.expectation_offset..state.expectation_offset + count],
            );
            state.expectation_offset += count;
            Ok(count)
        }
    }

    struct FixtureBootstrap(Rc<RefCell<FixtureState>>);

    impl Read for FixtureBootstrap {
        fn read(&mut self, target: &mut [u8]) -> io::Result<usize> {
            let mut state = self.0.borrow_mut();
            if matches!(state.bootstrap_mode, BootstrapMode::Broken) {
                return Err(io::Error::new(io::ErrorKind::BrokenPipe, "fixture broken"));
            }
            let exact_len = state.bootstrap.len();
            let effective_len = match state.bootstrap_mode {
                BootstrapMode::Short => exact_len.saturating_sub(1),
                BootstrapMode::Overlong => exact_len + 1,
                _ => exact_len,
            };
            if state.bootstrap_offset < effective_len {
                let count = (effective_len - state.bootstrap_offset)
                    .min(target.len())
                    .min(11);
                for (index, output) in target[..count].iter_mut().enumerate() {
                    let position = state.bootstrap_offset + index;
                    *output = state.bootstrap.get(position).copied().unwrap_or(0xA5);
                }
                state.bootstrap_offset += count;
                return Ok(count);
            }
            if matches!(state.bootstrap_mode, BootstrapMode::NoEof) {
                return Err(io::Error::new(io::ErrorKind::TimedOut, "fixture no eof"));
            }
            Ok(0)
        }
    }

    fn run_fixture(
        role: ChildBootstrapRole,
        measured: MeasuredChildContexts,
        mutation: ParentContextMutation,
        bootstrap_mode: BootstrapMode,
        control_fault: ControlFault,
        forced_expectation: Option<Vec<u8>>,
        nonce: BootstrapDigest,
        fail_on_call: Option<(usize, &'static str)>,
    ) -> (
        Result<ValidatedChildBootstrap, ChildHandshakeError>,
        Rc<RefCell<FixtureState>>,
        usize,
    ) {
        let inputs = inputs(role);
        let state = Rc::new(RefCell::new(FixtureState::new(
            &inputs,
            measured,
            mutation,
            bootstrap_mode,
            control_fault,
            forced_expectation,
        )));
        let mut control = FixtureControl(Rc::clone(&state));
        let mut bootstrap = FixtureBootstrap(Rc::clone(&state));
        let mut evidence = FakeEvidence {
            contexts: measured,
            validation_calls: 0,
            fail_on_call,
        };
        let result = perform_protocol(&inputs, &mut evidence, &mut control, &mut bootstrap, nonce);
        (result, state, evidence.validation_calls)
    }

    #[test]
    fn fake_evidence_completes_fragmented_ready_expectation_bootstrap_eof_and_ack_for_both_roles() {
        for (role, seed, nonce) in [
            (ChildBootstrapRole::LifecycleDriver, 0xA0, [0x21; 32]),
            (ChildBootstrapRole::BridgeLauncher, 0xB0, [0x31; 32]),
        ] {
            let (result, state, validation_calls) = run_fixture(
                role,
                contexts(seed),
                ParentContextMutation::None,
                BootstrapMode::ExactEof,
                ControlFault::None,
                None,
                nonce,
                None,
            );
            assert_eq!(result.unwrap().role(), role);
            assert_eq!(validation_calls, 4);
            let mut state = state.borrow_mut();
            assert_eq!(state.ready.len(), protocol::CHILD_READY_MESSAGE_LEN);
            assert_eq!(
                state.expectation.len(),
                protocol::CHILD_EXPECTATION_ENVELOPE_LEN
            );
            assert_eq!(state.bootstrap.len(), protocol::CHILD_BOOTSTRAP_FRAME_LEN);
            assert_eq!(state.ack.len(), protocol::CHILD_BOOTSTRAP_ACK_LEN);
            state.verify_ack().unwrap();
        }
    }

    #[test]
    fn bootstrap_requires_exact_336_bytes_followed_by_eof() {
        for (mode, expected) in [
            (
                BootstrapMode::Short,
                "child_bootstrap_transport_length_invalid",
            ),
            (
                BootstrapMode::Overlong,
                "child_bootstrap_transport_length_invalid",
            ),
            (
                BootstrapMode::NoEof,
                "child_bootstrap_transport_read_failed",
            ),
            (
                BootstrapMode::Broken,
                "child_bootstrap_transport_read_failed",
            ),
        ] {
            let (result, _, _) = run_fixture(
                ChildBootstrapRole::LifecycleDriver,
                contexts(0xA0),
                ParentContextMutation::None,
                mode,
                ControlFault::None,
                None,
                [0x21; 32],
                None,
            );
            assert_eq!(result.unwrap_err().code(), expected);
        }
    }

    #[test]
    fn timeout_broken_control_and_missing_local_job_proof_fail_before_later_states() {
        let (timeout, timeout_state, calls) = run_fixture(
            ChildBootstrapRole::LifecycleDriver,
            contexts(0xA0),
            ParentContextMutation::None,
            BootstrapMode::ExactEof,
            ControlFault::ReadyTimeout,
            None,
            [0x21; 32],
            None,
        );
        assert_eq!(timeout.unwrap_err().code(), "child_ready_write_failed");
        assert_eq!(calls, 1);
        assert!(timeout_state.borrow().ready.is_empty());

        let (broken, broken_state, _) = run_fixture(
            ChildBootstrapRole::LifecycleDriver,
            contexts(0xA0),
            ParentContextMutation::None,
            BootstrapMode::ExactEof,
            ControlFault::ExpectationBroken,
            None,
            [0x21; 32],
            None,
        );
        assert_eq!(broken.unwrap_err().code(), "child_expectation_read_failed");
        assert!(broken_state.borrow().ack.is_empty());

        let (no_job, no_job_state, _) = run_fixture(
            ChildBootstrapRole::LifecycleDriver,
            contexts(0xA0),
            ParentContextMutation::None,
            BootstrapMode::ExactEof,
            ControlFault::None,
            None,
            [0x21; 32],
            Some((1, "child_handshake_job_membership_required")),
        );
        assert_eq!(
            no_job.unwrap_err().code(),
            "child_handshake_job_membership_required"
        );
        assert!(no_job_state.borrow().ready.is_empty());
    }

    #[test]
    fn child_observable_context_and_server_drift_never_create_an_ack() {
        for mutation in [
            ParentContextMutation::RunnerToken,
            ParentContextMutation::ControlServer,
        ] {
            let (result, state, _) = run_fixture(
                ChildBootstrapRole::LifecycleDriver,
                contexts(0xA0),
                mutation,
                BootstrapMode::ExactEof,
                ControlFault::None,
                None,
                [0x21; 32],
                None,
            );
            assert_eq!(
                result.unwrap_err().code(),
                "child_handshake_peer_context_unexpected"
            );
            assert!(state.borrow().ack.is_empty());
        }
    }

    #[test]
    fn replayed_expectation_wrong_role_and_bad_ack_mac_are_rejected() {
        let (first, first_state, _) = run_fixture(
            ChildBootstrapRole::LifecycleDriver,
            contexts(0xA0),
            ParentContextMutation::None,
            BootstrapMode::ExactEof,
            ControlFault::None,
            None,
            [0x21; 32],
            None,
        );
        first.unwrap();
        let replay = first_state.borrow().expectation.clone();
        let (second, second_state, _) = run_fixture(
            ChildBootstrapRole::LifecycleDriver,
            contexts(0xA0),
            ParentContextMutation::None,
            BootstrapMode::ExactEof,
            ControlFault::None,
            Some(replay),
            [0x22; 32],
            None,
        );
        assert!(second.is_err());
        assert!(second_state.borrow().ack.is_empty());

        let mut bad_expectation_mac = first_state.borrow().expectation.clone();
        *bad_expectation_mac.last_mut().unwrap() ^= 0x40;
        let (bad_mac, bad_mac_state, _) = run_fixture(
            ChildBootstrapRole::LifecycleDriver,
            contexts(0xA0),
            ParentContextMutation::None,
            BootstrapMode::ExactEof,
            ControlFault::None,
            Some(bad_expectation_mac),
            [0x21; 32],
            None,
        );
        assert!(bad_mac.is_err());
        assert!(bad_mac_state.borrow().ack.is_empty());

        let (bridge, bridge_state, _) = run_fixture(
            ChildBootstrapRole::BridgeLauncher,
            contexts(0xB0),
            ParentContextMutation::None,
            BootstrapMode::ExactEof,
            ControlFault::None,
            None,
            [0x31; 32],
            None,
        );
        bridge.unwrap();
        let wrong_role_expectation = bridge_state.borrow().expectation.clone();
        let (wrong_role, wrong_role_state, _) = run_fixture(
            ChildBootstrapRole::LifecycleDriver,
            contexts(0xA0),
            ParentContextMutation::None,
            BootstrapMode::ExactEof,
            ControlFault::None,
            Some(wrong_role_expectation),
            [0x21; 32],
            None,
        );
        assert!(wrong_role.is_err());
        assert!(wrong_role_state.borrow().ack.is_empty());

        assert_eq!(
            validate_expected_role(
                ChildBootstrapRole::LifecycleDriver,
                ChildBootstrapRole::BridgeLauncher,
            )
            .unwrap_err()
            .code(),
            "child_handshake_role_unexpected"
        );

        let mut first_state = first_state.borrow_mut();
        let last = first_state.ack.last_mut().unwrap();
        *last ^= 0x80;
        assert_eq!(
            first_state.verify_ack().unwrap_err().code(),
            "child_bootstrap_ack_mac_invalid"
        );
    }

    #[test]
    fn canonical_observed_launch_changes_with_primary_thread_id_and_epoch() {
        let base = observed_launch_source_digest(
            ChildBootstrapRole::LifecycleDriver,
            101,
            201,
            301,
            401,
            &[0x11; 32],
            &[0x22; 32],
            &[0x33; 32],
            &[0x44; 32],
            &[0x55; 32],
            &[0x66; 32],
        );
        let changed_id = observed_launch_source_digest(
            ChildBootstrapRole::LifecycleDriver,
            101,
            201,
            302,
            401,
            &[0x11; 32],
            &[0x22; 32],
            &[0x33; 32],
            &[0x44; 32],
            &[0x55; 32],
            &[0x66; 32],
        );
        let changed_epoch = observed_launch_source_digest(
            ChildBootstrapRole::LifecycleDriver,
            101,
            201,
            301,
            402,
            &[0x11; 32],
            &[0x22; 32],
            &[0x33; 32],
            &[0x44; 32],
            &[0x55; 32],
            &[0x66; 32],
        );
        assert_ne!(base, changed_id);
        assert_ne!(base, changed_epoch);
    }

    #[test]
    fn denied_control_server_query_handle_has_no_weaker_fallback() {
        for raw in [null_mut(), INVALID_HANDLE_VALUE] {
            assert_eq!(
                require_process_observation_handle(raw).unwrap_err().code(),
                "child_handshake_process_open_failed"
            );
        }
    }

    #[test]
    fn exited_process_with_still_active_numeric_code_is_not_live() {
        let system_root = std::env::var_os("SystemRoot").expect("SystemRoot");
        let executable = PathBuf::from(system_root).join("System32").join("cmd.exe");
        let mut child = Command::new(executable)
            .args(["/d", "/c", "exit 259"])
            .creation_flags(CREATE_NO_WINDOW)
            .spawn()
            .expect("start fixed exit-code child");
        let status = child.wait().expect("wait fixed exit-code child");
        assert_eq!(status.code(), Some(STILL_ACTIVE as i32));

        let process = child.as_raw_handle().cast();
        let mut observed = 0u32;
        assert_ne!(unsafe { GetExitCodeProcess(process, &mut observed) }, 0);
        assert_eq!(observed, STILL_ACTIVE as u32);
        assert_eq!(
            require_process_still_running(process).unwrap_err().code(),
            "child_handshake_process_not_running"
        );
    }

    #[test]
    fn source_has_no_unbounded_or_half_close_pipe_operations() {
        let source = include_str!("primitive_evidence_child_handshake_windows.rs");
        assert!(!source.contains(&["Flush", "FileBuffers"].concat()));
        assert!(!source.contains(&["Disconnect", "NamedPipe"].concat()));
        assert!(source.contains("CancelIoEx"));
        assert!(source.contains("GetOverlappedResult"));
    }
}

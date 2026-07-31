use super::{
    bootstrap_activation::{self, CandidateProcessEvidence},
    candidate_activation_orchestrator::CandidateValidationEvidence,
    worker_store_windows::open_candidate_consumption_tombstone,
    AuthorityMaintenanceError,
};
use crate::primitive_evidence_authority_windows::{
    inspect_installed_authority_for_generation, AuthorityLayout,
};
use sha2::{Digest, Sha256};
use std::{
    ffi::OsString,
    fs::{File, OpenOptions},
    io::{Read, Seek, SeekFrom},
    mem::zeroed,
    os::windows::{
        ffi::OsStringExt,
        fs::{MetadataExt, OpenOptionsExt},
        io::{AsRawHandle, FromRawHandle, OwnedHandle, RawHandle},
    },
    path::{Path, PathBuf},
    ptr,
    time::{Duration, Instant},
};
use windows_sys::Win32::{
    Foundation::{
        GetLastError, ERROR_BROKEN_PIPE, ERROR_IO_PENDING, ERROR_MORE_DATA,
        ERROR_OPERATION_ABORTED, GENERIC_READ, GENERIC_WRITE, HANDLE, INVALID_HANDLE_VALUE,
        STILL_ACTIVE, WAIT_OBJECT_0, WAIT_TIMEOUT,
    },
    Storage::FileSystem::{
        CreateFileW, GetFileInformationByHandle, GetFinalPathNameByHandleW, ReadFile, WriteFile,
        BY_HANDLE_FILE_INFORMATION, FILE_ATTRIBUTE_DIRECTORY, FILE_ATTRIBUTE_REPARSE_POINT,
        FILE_FLAG_OPEN_REPARSE_POINT, FILE_FLAG_OVERLAPPED, FILE_FLAG_SEQUENTIAL_SCAN,
        FILE_SHARE_DELETE, FILE_SHARE_READ, FILE_SHARE_WRITE, OPEN_EXISTING, SYNCHRONIZE,
    },
    System::{
        Pipes::{GetNamedPipeServerProcessId, WaitNamedPipeW},
        Threading::{
            CreateEventW, GetExitCodeProcess, GetProcessId, GetProcessTimes, OpenProcess,
            QueryFullProcessImageNameW, WaitForMultipleObjects, WaitForSingleObject,
            PROCESS_QUERY_LIMITED_INFORMATION,
        },
        IO::{CancelIoEx, GetOverlappedResult, OVERLAPPED},
    },
};

const CANDIDATE_CLIENT_IO_TIMEOUT_MILLIS: u32 =
    bootstrap_activation::CANDIDATE_HANDSHAKE_WINDOW_MILLIS;
const CANDIDATE_CLOSEOUT_TIMEOUT: Duration =
    Duration::from_millis(bootstrap_activation::CANDIDATE_HANDSHAKE_CLOSEOUT_GRACE_MILLIS as u64);
const CANDIDATE_CLOSEOUT_POLL_INTERVAL: Duration = Duration::from_millis(25);
const MAX_CANDIDATE_SERVER_IMAGE_BYTES: u64 = 512 * 1024 * 1024;
const CANDIDATE_CLIENT_TRANSCRIPT_DOMAIN: &[u8] =
    b"vrcforge-authority-candidate-client-transcript-v1\0";
const CANDIDATE_STOPPED_READBACK_DOMAIN: &[u8] =
    b"vrcforge-authority-candidate-stopped-readback-v1\0";

#[derive(Debug, PartialEq, Eq)]
struct NativeCandidateClientTranscript {
    server: CandidateProcessEvidence,
    pipe_instance_id: [u8; 16],
    request_bytes: Vec<u8>,
    response_bytes: Vec<u8>,
    transcript_sha256: [u8; 32],
}

#[derive(Debug, PartialEq, Eq)]
struct NativeCandidateStoppedProof {
    generation: [u8; 32],
    server: CandidateProcessEvidence,
    process_exit_code: u32,
}

impl NativeCandidateStoppedProof {
    fn digest(&self) -> Result<[u8; 32], AuthorityMaintenanceError> {
        self.server
            .validate()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        if self.generation.iter().all(|byte| *byte == 0) || self.process_exit_code != 0 {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_client_stopped_proof_invalid",
            ));
        }
        let mut digest = Sha256::new();
        digest.update(CANDIDATE_STOPPED_READBACK_DOMAIN);
        digest.update(self.generation);
        digest.update(self.server.process_id().to_be_bytes());
        digest.update(self.server.process_creation_time().to_be_bytes());
        digest.update(self.server.image_sha256());
        digest.update(self.server.image_byte_length().to_be_bytes());
        digest.update(self.server.image_volume_serial().to_be_bytes());
        digest.update(self.server.image_file_id());
        digest.update(self.server.image_link_count().to_be_bytes());
        digest.update(self.server.image_attributes().to_be_bytes());
        digest.update(self.server.full_readback_receipt_sha256());
        digest.update(self.process_exit_code.to_be_bytes());
        // The constructor is reached only after the exact SCM readback predicate:
        // STOPPED, no service PID, and zero Win32/service-specific exit codes.
        digest.update(1u32.to_be_bytes());
        digest.update(0u32.to_be_bytes());
        digest.update(0u32.to_be_bytes());
        digest.update(0u32.to_be_bytes());
        Ok(digest.finalize().into())
    }
}

struct NativeCandidateExchange {
    transcript: NativeCandidateClientTranscript,
    server: HeldCandidateServer,
}

/// Drives the fixed one-shot request from an exact Armed record. Response
/// bytes remain untrusted until the actual pipe server process/image evidence
/// and pre-exchange START_PENDING SCM readback match. A verified response is
/// returned only after that held process exits and SCM reports exact successful
/// STOPPED state within the closeout budget.
pub(super) fn validate_armed_candidate_once(
    armed: &bootstrap_activation::CandidateCredentialRecord,
) -> Result<CandidateValidationEvidence, AuthorityMaintenanceError> {
    if armed.phase() != bootstrap_activation::CandidateCredentialPhase::Armed {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_client_credential_not_armed",
        ));
    }
    let binding = armed
        .binding()
        .map_err(|error| AuthorityMaintenanceError(error.code()))?;
    let expected_server = *armed.candidate_service().ok_or(AuthorityMaintenanceError(
        "authority_candidate_service_process_missing",
    ))?;
    expected_server
        .validate()
        .map_err(|error| AuthorityMaintenanceError(error.code()))?;
    require_start_pending_scm(binding.generation(), expected_server.process_id())?;
    let request = bootstrap_activation::CandidateValidationRequest::new(
        binding.credential_sha256(),
        *binding.nonce(),
    )
    .map_err(|error| AuthorityMaintenanceError(error.code()))?;
    let request_bytes = request
        .canonical_bytes()
        .map_err(|error| AuthorityMaintenanceError(error.code()))?;
    let pipe_instance_id = binding.pipe_instance_id();
    let exchange =
        exchange_candidate_validation_once(pipe_instance_id, expected_server, &request_bytes)?;
    let transcript = &exchange.transcript;
    if transcript.pipe_instance_id != pipe_instance_id
        || transcript.request_bytes != request_bytes
        || transcript.server != expected_server
        || transcript.transcript_sha256
            != transcript_digest(
                &transcript.pipe_instance_id,
                &transcript.server,
                &transcript.request_bytes,
                &transcript.response_bytes,
            )
    {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_client_transcript_mismatch",
        ));
    }
    let peer = bootstrap_activation::CandidateServicePeerObservation::new(
        transcript.server,
        pipe_instance_id,
    )
    .map_err(|error| AuthorityMaintenanceError(error.code()))?;
    let expectation = bootstrap_activation::CandidateResponseExpectation::new(
        binding,
        request,
        expected_server,
        peer,
    )
    .map_err(|error| AuthorityMaintenanceError(error.code()))?;
    let receipt = bootstrap_activation::UntrustedCandidateValidationResponse::parse_canonical(
        &transcript.response_bytes,
    )
    .and_then(|response| response.verify_against(&expectation))
    .map_err(|error| AuthorityMaintenanceError(error.code()))?;
    let stopped =
        wait_for_candidate_stopped_scm(binding.generation(), expected_server, exchange.server)?;
    let layout = AuthorityLayout::installed()
        .map_err(|_| AuthorityMaintenanceError("authority_candidate_client_layout_unavailable"))?;
    let tombstone = open_candidate_consumption_tombstone(&layout, receipt.credential_sha256())?
        .ok_or(AuthorityMaintenanceError(
            "authority_candidate_consumption_tombstone_missing_after_verified_response",
        ))?;
    let consumed =
        bootstrap_activation::CandidateCredentialRecord::parse_canonical(tombstone.bytes())
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
    let (
        tombstone_file_volume_serial,
        tombstone_file_id,
        tombstone_file_link_count,
        tombstone_file_sha256,
    ) = tombstone.durable_identity_with_link_count();
    let evidence = CandidateValidationEvidence::from_verified_observation(
        armed,
        receipt,
        exchange.transcript.transcript_sha256,
        &consumed,
        tombstone_file_sha256,
        tombstone_file_volume_serial,
        tombstone_file_id,
        tombstone_file_link_count,
        stopped.digest()?,
    )?;
    drop(tombstone);
    Ok(evidence)
}

fn require_start_pending_scm(
    generation: &[u8; 32],
    expected_process_id: u32,
) -> Result<(), AuthorityMaintenanceError> {
    let layout = AuthorityLayout::installed()
        .map_err(|_| AuthorityMaintenanceError("authority_candidate_client_layout_unavailable"))?;
    let readback = inspect_installed_authority_for_generation(&layout, generation)
        .map_err(|_| AuthorityMaintenanceError("authority_candidate_client_scm_readback_failed"))?;
    if !readback
        .candidate_service_configuration_exact_for_start_pending_process(expected_process_id)
    {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_client_scm_start_pending_mismatch",
        ));
    }
    Ok(())
}

fn wait_for_candidate_stopped_scm(
    generation: &[u8; 32],
    expected_server: CandidateProcessEvidence,
    server: HeldCandidateServer,
) -> Result<NativeCandidateStoppedProof, AuthorityMaintenanceError> {
    if server.evidence()? != expected_server {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_client_server_process_mismatch",
        ));
    }
    let deadline = Instant::now()
        .checked_add(CANDIDATE_CLOSEOUT_TIMEOUT)
        .ok_or(AuthorityMaintenanceError(
            "authority_candidate_client_closeout_deadline_invalid",
        ))?;
    let remaining = candidate_closeout_remaining(deadline)?;
    let wait_millis = remaining.as_millis().clamp(1, u128::from(u32::MAX)) as u32;
    match unsafe { WaitForSingleObject(server.raw_process(), wait_millis) } {
        WAIT_OBJECT_0 => {}
        WAIT_TIMEOUT => {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_client_server_exit_timeout",
            ))
        }
        _ => {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_client_server_exit_wait_failed",
            ))
        }
    }
    let mut process_exit_code = STILL_ACTIVE as u32;
    if unsafe { GetExitCodeProcess(server.raw_process(), &mut process_exit_code) } == 0
        || process_exit_code != 0
    {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_client_server_exit_invalid",
        ));
    }

    let layout = AuthorityLayout::installed()
        .map_err(|_| AuthorityMaintenanceError("authority_candidate_client_layout_unavailable"))?;
    loop {
        candidate_closeout_remaining(deadline)?;
        let readback =
            inspect_installed_authority_for_generation(&layout, generation).map_err(|_| {
                AuthorityMaintenanceError("authority_candidate_client_scm_readback_failed")
            })?;
        if readback.candidate_service_configuration_exact_for_stopped_success() {
            candidate_closeout_remaining(deadline)?;
            return Ok(NativeCandidateStoppedProof {
                generation: *generation,
                server: expected_server,
                process_exit_code,
            });
        }
        if !readback.candidate_service_configuration_exact_for_start_pending_process(
            expected_server.process_id(),
        ) {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_client_scm_stopped_mismatch",
            ));
        }
        let remaining = candidate_closeout_remaining(deadline)?;
        std::thread::sleep(CANDIDATE_CLOSEOUT_POLL_INTERVAL.min(remaining));
    }
}

fn candidate_closeout_remaining(deadline: Instant) -> Result<Duration, AuthorityMaintenanceError> {
    let remaining = deadline.saturating_duration_since(Instant::now());
    if remaining.is_zero() {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_client_scm_stopped_timeout",
        ));
    }
    Ok(remaining)
}

fn exchange_candidate_validation_once(
    pipe_instance_id: [u8; 16],
    expected_server: CandidateProcessEvidence,
    request_bytes: &[u8],
) -> Result<NativeCandidateExchange, AuthorityMaintenanceError> {
    expected_server
        .validate()
        .map_err(|_| AuthorityMaintenanceError("authority_candidate_server_expectation_invalid"))?;
    if expected_server.image_byte_length() > MAX_CANDIDATE_SERVER_IMAGE_BYTES {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_server_expectation_invalid",
        ));
    }
    if pipe_instance_id.iter().all(|byte| *byte == 0)
        || request_bytes.is_empty()
        || request_bytes.len() > bootstrap_activation::MAX_CANDIDATE_HANDSHAKE_BYTES
    {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_client_request_invalid",
        ));
    }
    bootstrap_activation::CandidateValidationRequest::parse_canonical(request_bytes)
        .map_err(|_| AuthorityMaintenanceError("authority_candidate_client_request_invalid"))?;
    let pipe_name = bootstrap_activation::candidate_pipe_name(&pipe_instance_id)
        .map_err(|_| AuthorityMaintenanceError("authority_candidate_client_pipe_invalid"))?;
    let pipe_name = pipe_name
        .encode_utf16()
        .chain(std::iter::once(0))
        .collect::<Vec<_>>();
    if unsafe { WaitNamedPipeW(pipe_name.as_ptr(), CANDIDATE_CLIENT_IO_TIMEOUT_MILLIS) } == 0 {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_client_pipe_unavailable",
        ));
    }
    let pipe = unsafe {
        CreateFileW(
            pipe_name.as_ptr(),
            GENERIC_READ | GENERIC_WRITE,
            0,
            ptr::null(),
            OPEN_EXISTING,
            FILE_FLAG_OVERLAPPED,
            ptr::null_mut(),
        )
    };
    if pipe == INVALID_HANDLE_VALUE {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_client_pipe_open_failed",
        ));
    }
    let pipe = unsafe { OwnedHandle::from_raw_handle(pipe as RawHandle) };
    let mut server_process_id = 0u32;
    if unsafe { GetNamedPipeServerProcessId(pipe.as_raw_handle().cast(), &mut server_process_id) }
        == 0
        || server_process_id != expected_server.process_id()
    {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_client_server_process_mismatch",
        ));
    }
    let server_process = unsafe {
        OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE,
            0,
            server_process_id,
        )
    };
    if server_process.is_null() {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_client_server_process_unavailable",
        ));
    }
    let server_process = unsafe { OwnedHandle::from_raw_handle(server_process as RawHandle) };
    let mut server = HeldCandidateServer::open(server_process, expected_server)?;
    server.revalidate(true)?;

    write_overlapped(
        pipe.as_raw_handle().cast(),
        server.raw_process(),
        request_bytes,
    )?;
    let response_bytes = read_overlapped(
        pipe.as_raw_handle().cast(),
        server.raw_process(),
        bootstrap_activation::MAX_CANDIDATE_HANDSHAKE_BYTES,
    )?;
    server.revalidate(true)?;
    let mut server_process_id_after = 0u32;
    if unsafe {
        GetNamedPipeServerProcessId(pipe.as_raw_handle().cast(), &mut server_process_id_after)
    } == 0
        || server_process_id_after != server_process_id
    {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_client_server_process_changed",
        ));
    }
    let observed_server = server.evidence()?;
    bootstrap_activation::UntrustedCandidateValidationResponse::parse_canonical(&response_bytes)
        .map_err(|_| AuthorityMaintenanceError("authority_candidate_client_response_invalid"))?;

    let transcript_sha256 = transcript_digest(
        &pipe_instance_id,
        &observed_server,
        request_bytes,
        &response_bytes,
    );
    let transcript = NativeCandidateClientTranscript {
        server: observed_server,
        pipe_instance_id,
        request_bytes: request_bytes.to_vec(),
        response_bytes,
        transcript_sha256,
    };
    drop(pipe);
    Ok(NativeCandidateExchange { transcript, server })
}

pub(super) struct HeldCandidateServer {
    process: OwnedHandle,
    process_id: u32,
    process_creation_time: u64,
    image: HeldServerImage,
    expected: CandidateProcessEvidence,
}

impl HeldCandidateServer {
    fn open(
        process: OwnedHandle,
        expected: CandidateProcessEvidence,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let raw = process.as_raw_handle().cast();
        if unsafe { GetProcessId(raw) } != expected.process_id()
            || process_creation_time(raw)? != expected.process_creation_time()
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_client_server_process_mismatch",
            ));
        }
        let image = HeldServerImage::open(raw)?;
        if !image.matches_expectation(&expected) {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_client_server_image_mismatch",
            ));
        }
        Ok(Self {
            process,
            process_id: expected.process_id(),
            process_creation_time: expected.process_creation_time(),
            image,
            expected,
        })
    }

    pub(super) fn open_started(
        process: OwnedHandle,
        process_id: u32,
        expected_image: bootstrap_activation::CandidateImageEvidence,
        image: HeldServerImage,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let raw = process.as_raw_handle().cast();
        if process_id == 0 || unsafe { GetProcessId(raw) } != process_id {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_process_mismatch",
            ));
        }
        let process_creation_time = process_creation_time(raw)?;
        let expected = CandidateProcessEvidence::from_static_image(
            process_id,
            process_creation_time,
            expected_image,
        )
        .map_err(|_| AuthorityMaintenanceError("authority_candidate_start_process_invalid"))?;
        if !image.matches_expectation(&expected)
            || !paths_equal(&process_image_path(raw)?, &image.path)
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_image_mismatch",
            ));
        }
        let mut value = Self {
            process,
            process_id,
            process_creation_time,
            image,
            expected,
        };
        value.revalidate(true)?;
        Ok(value)
    }

    pub(super) fn raw_process(&self) -> HANDLE {
        self.process.as_raw_handle().cast()
    }

    pub(super) fn evidence(&self) -> Result<CandidateProcessEvidence, AuthorityMaintenanceError> {
        CandidateProcessEvidence::from_held_process(
            self.process_id,
            self.process_creation_time,
            self.image.sha256,
            self.image.identity.byte_length,
            self.image.identity.volume_serial,
            self.image.identity.file_id,
            self.image.identity.link_count,
            self.image.identity.attributes,
        )
        .map_err(|_| AuthorityMaintenanceError("authority_candidate_server_evidence_invalid"))
    }

    pub(super) fn revalidate(
        &mut self,
        require_active: bool,
    ) -> Result<(), AuthorityMaintenanceError> {
        let raw = self.raw_process();
        if unsafe { GetProcessId(raw) } != self.process_id
            || process_creation_time(raw)? != self.process_creation_time
            || (require_active && unsafe { WaitForSingleObject(raw, 0) } != WAIT_TIMEOUT)
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_client_server_process_changed",
            ));
        }
        self.image.revalidate_process(raw)?;
        if !self.image.matches_expectation(&self.expected) {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_client_server_image_changed",
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct ServerImageIdentity {
    volume_serial: u64,
    file_id: [u8; 16],
    byte_length: u64,
    link_count: u32,
    attributes: u32,
}

pub(super) struct HeldServerImage {
    file: File,
    path: PathBuf,
    identity: ServerImageIdentity,
    sha256: [u8; 32],
}

impl HeldServerImage {
    fn open(process: HANDLE) -> Result<Self, AuthorityMaintenanceError> {
        let path = process_image_path(process)?;
        Self::open_path_with_share(
            &path,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        )
    }

    /// Opens the pre-start image without write/delete sharing so its directory
    /// entry and bytes cannot be replaced between static validation and SCM
    /// process binding.
    pub(super) fn open_path(path: &Path) -> Result<Self, AuthorityMaintenanceError> {
        Self::open_path_with_share(path, FILE_SHARE_READ)
    }

    fn open_path_with_share(
        path: &Path,
        share_mode: u32,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let metadata = std::fs::symlink_metadata(&path).map_err(|_| {
            AuthorityMaintenanceError("authority_candidate_client_server_image_metadata_failed")
        })?;
        if !metadata.is_file()
            || metadata.file_type().is_symlink()
            || metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
            || metadata.len() == 0
            || metadata.len() > MAX_CANDIDATE_SERVER_IMAGE_BYTES
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_client_server_image_metadata_invalid",
            ));
        }
        let mut file = OpenOptions::new()
            .read(true)
            .share_mode(share_mode)
            .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_SEQUENTIAL_SCAN)
            .open(path)
            .map_err(|_| {
                AuthorityMaintenanceError("authority_candidate_client_server_image_open_failed")
            })?;
        require_exact_handle_path(&file, path)?;
        let (identity, sha256) = read_server_image(&mut file)?;
        if identity.link_count != 1
            || identity.attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT) != 0
            || identity.byte_length != metadata.len()
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_client_server_image_identity_invalid",
            ));
        }
        Ok(Self {
            file,
            path: path.to_path_buf(),
            identity,
            sha256,
        })
    }

    fn matches_expectation(&self, expected: &CandidateProcessEvidence) -> bool {
        self.matches_image_expectation(expected.image())
    }

    pub(super) fn require_static_expectation(
        &self,
        expected: &bootstrap_activation::CandidateImageEvidence,
    ) -> Result<(), AuthorityMaintenanceError> {
        if !self.matches_image_expectation(expected) {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_start_image_mismatch",
            ));
        }
        Ok(())
    }

    fn matches_image_expectation(
        &self,
        expected: &bootstrap_activation::CandidateImageEvidence,
    ) -> bool {
        self.sha256 == *expected.image_sha256()
            && self.identity.byte_length == expected.image_byte_length()
            && self.identity.volume_serial == expected.image_volume_serial()
            && self.identity.file_id == *expected.image_file_id()
            && self.identity.link_count == expected.image_link_count()
            && self.identity.attributes == expected.image_attributes()
    }

    pub(super) fn revalidate_process(
        &mut self,
        process: HANDLE,
    ) -> Result<(), AuthorityMaintenanceError> {
        let process_path = process_image_path(process)?;
        if !paths_equal(&process_path, &self.path) {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_client_server_image_path_changed",
            ));
        }
        self.revalidate_held()?;
        let mut current = Self::open_path_with_share(
            &process_path,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        )?;
        current.revalidate_held()?;
        if current.identity != self.identity
            || current.sha256 != self.sha256
            || !paths_equal(&current.path, &self.path)
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_client_server_image_changed",
            ));
        }
        Ok(())
    }

    pub(super) fn revalidate_held(&mut self) -> Result<(), AuthorityMaintenanceError> {
        require_exact_handle_path(&self.file, &self.path)?;
        let (identity, sha256) = read_server_image(&mut self.file)?;
        if identity != self.identity || sha256 != self.sha256 {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_client_server_image_changed",
            ));
        }
        Ok(())
    }
}

fn write_overlapped(
    pipe: HANDLE,
    process: HANDLE,
    bytes: &[u8],
) -> Result<(), AuthorityMaintenanceError> {
    let mut operation = OverlappedOperation::new()?;
    let mut written = 0u32;
    let started = unsafe {
        WriteFile(
            pipe,
            bytes.as_ptr().cast(),
            bytes.len() as u32,
            &mut written,
            &mut operation.overlapped,
        )
    };
    let written = if started != 0 {
        written
    } else if unsafe { GetLastError() } == ERROR_IO_PENDING {
        wait_overlapped(pipe, process, &mut operation)?
    } else {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_client_write_failed",
        ));
    };
    if written as usize != bytes.len() {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_client_write_truncated",
        ));
    }
    Ok(())
}

fn read_overlapped(
    pipe: HANDLE,
    process: HANDLE,
    maximum: usize,
) -> Result<Vec<u8>, AuthorityMaintenanceError> {
    let mut bytes = vec![0u8; maximum + 1];
    let mut operation = OverlappedOperation::new()?;
    let mut read = 0u32;
    let started = unsafe {
        ReadFile(
            pipe,
            bytes.as_mut_ptr().cast(),
            bytes.len() as u32,
            &mut read,
            &mut operation.overlapped,
        )
    };
    let read = if started != 0 {
        read
    } else {
        match unsafe { GetLastError() } {
            ERROR_IO_PENDING => wait_overlapped(pipe, process, &mut operation)?,
            ERROR_MORE_DATA => {
                return Err(AuthorityMaintenanceError(
                    "authority_candidate_client_response_too_large",
                ))
            }
            ERROR_BROKEN_PIPE | ERROR_OPERATION_ABORTED => {
                return Err(AuthorityMaintenanceError(
                    "authority_candidate_client_response_unavailable",
                ))
            }
            _ => {
                return Err(AuthorityMaintenanceError(
                    "authority_candidate_client_read_failed",
                ))
            }
        }
    } as usize;
    if read == 0 || read > maximum {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_client_response_size_invalid",
        ));
    }
    bytes.truncate(read);
    Ok(bytes)
}

struct OverlappedOperation {
    event: OwnedHandle,
    overlapped: OVERLAPPED,
}

impl OverlappedOperation {
    fn new() -> Result<Self, AuthorityMaintenanceError> {
        let event = unsafe { CreateEventW(ptr::null(), 1, 0, ptr::null()) };
        if event.is_null() {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_client_event_create_failed",
            ));
        }
        let event = unsafe { OwnedHandle::from_raw_handle(event as RawHandle) };
        let mut overlapped = unsafe { zeroed::<OVERLAPPED>() };
        overlapped.hEvent = event.as_raw_handle().cast();
        Ok(Self { event, overlapped })
    }
}

fn wait_overlapped(
    pipe: HANDLE,
    process: HANDLE,
    operation: &mut OverlappedOperation,
) -> Result<u32, AuthorityMaintenanceError> {
    let handles = [operation.event.as_raw_handle().cast(), process];
    let wait = unsafe {
        WaitForMultipleObjects(
            handles.len() as u32,
            handles.as_ptr(),
            0,
            CANDIDATE_CLIENT_IO_TIMEOUT_MILLIS,
        )
    };
    if wait != WAIT_OBJECT_0 {
        unsafe {
            CancelIoEx(pipe, &operation.overlapped);
        }
        return Err(AuthorityMaintenanceError(if wait == WAIT_OBJECT_0 + 1 {
            "authority_candidate_client_server_exited"
        } else {
            "authority_candidate_client_io_timeout"
        }));
    }
    let mut transferred = 0u32;
    if unsafe { GetOverlappedResult(pipe, &operation.overlapped, &mut transferred, 0) } == 0 {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_client_io_failed",
        ));
    }
    Ok(transferred)
}

fn process_creation_time(process: HANDLE) -> Result<u64, AuthorityMaintenanceError> {
    let mut creation = unsafe { zeroed() };
    let mut exit = unsafe { zeroed() };
    let mut kernel = unsafe { zeroed() };
    let mut user = unsafe { zeroed() };
    if unsafe { GetProcessTimes(process, &mut creation, &mut exit, &mut kernel, &mut user) } == 0 {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_client_server_process_times_unavailable",
        ));
    }
    let value = (u64::from(creation.dwHighDateTime) << 32) | u64::from(creation.dwLowDateTime);
    if value == 0 {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_client_server_process_times_invalid",
        ));
    }
    Ok(value)
}

fn process_image_path(process: HANDLE) -> Result<PathBuf, AuthorityMaintenanceError> {
    let mut words = vec![0u16; 32_768];
    let mut length = words.len() as u32;
    if unsafe { QueryFullProcessImageNameW(process, 0, words.as_mut_ptr(), &mut length) } == 0
        || length == 0
        || length as usize >= words.len()
    {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_client_server_image_path_unavailable",
        ));
    }
    words.truncate(length as usize);
    if words.contains(&0) {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_client_server_image_path_unavailable",
        ));
    }
    Ok(PathBuf::from(OsString::from_wide(&words)))
}

fn read_server_image(
    file: &mut File,
) -> Result<(ServerImageIdentity, [u8; 32]), AuthorityMaintenanceError> {
    let before = server_image_identity(file)?;
    if before.byte_length == 0 || before.byte_length > MAX_CANDIDATE_SERVER_IMAGE_BYTES {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_client_server_image_size_invalid",
        ));
    }
    file.seek(SeekFrom::Start(0)).map_err(|_| {
        AuthorityMaintenanceError("authority_candidate_client_server_image_read_failed")
    })?;
    let mut digest = Sha256::new();
    let mut buffer = [0u8; 64 * 1024];
    let mut length = 0u64;
    loop {
        let count = file.read(&mut buffer).map_err(|_| {
            AuthorityMaintenanceError("authority_candidate_client_server_image_read_failed")
        })?;
        if count == 0 {
            break;
        }
        length = length
            .checked_add(count as u64)
            .ok_or(AuthorityMaintenanceError(
                "authority_candidate_client_server_image_size_invalid",
            ))?;
        if length > MAX_CANDIDATE_SERVER_IMAGE_BYTES {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_client_server_image_size_invalid",
            ));
        }
        digest.update(&buffer[..count]);
    }
    file.seek(SeekFrom::Start(0)).map_err(|_| {
        AuthorityMaintenanceError("authority_candidate_client_server_image_read_failed")
    })?;
    let after = server_image_identity(file)?;
    if before != after || length != before.byte_length {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_client_server_image_changed",
        ));
    }
    Ok((before, digest.finalize().into()))
}

fn server_image_identity(file: &File) -> Result<ServerImageIdentity, AuthorityMaintenanceError> {
    let mut information: BY_HANDLE_FILE_INFORMATION = unsafe { zeroed() };
    if unsafe { GetFileInformationByHandle(file.as_raw_handle().cast(), &mut information) } == 0 {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_client_server_image_identity_unavailable",
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
        return Err(AuthorityMaintenanceError(
            "authority_candidate_client_server_image_identity_invalid",
        ));
    }
    Ok(ServerImageIdentity {
        volume_serial,
        file_id,
        byte_length,
        link_count: information.nNumberOfLinks,
        attributes: information.dwFileAttributes,
    })
}

fn transcript_digest(
    pipe_instance_id: &[u8; 16],
    server: &CandidateProcessEvidence,
    request: &[u8],
    response: &[u8],
) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(CANDIDATE_CLIENT_TRANSCRIPT_DOMAIN);
    digest.update(pipe_instance_id);
    digest.update(server.process_id().to_be_bytes());
    digest.update(server.process_creation_time().to_be_bytes());
    digest.update(server.image_sha256());
    digest.update(server.image_byte_length().to_be_bytes());
    digest.update(server.image_volume_serial().to_be_bytes());
    digest.update(server.image_file_id());
    digest.update(server.image_link_count().to_be_bytes());
    digest.update(server.image_attributes().to_be_bytes());
    digest.update(server.full_readback_receipt_sha256());
    digest.update((request.len() as u64).to_be_bytes());
    digest.update(request);
    digest.update((response.len() as u64).to_be_bytes());
    digest.update(response);
    digest.finalize().into()
}

fn require_exact_handle_path(
    file: &File,
    expected: &Path,
) -> Result<(), AuthorityMaintenanceError> {
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
        return Err(AuthorityMaintenanceError(
            "authority_candidate_client_server_image_path_readback_failed",
        ));
    }
    words.truncate(length);
    if words.contains(&0) {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_client_server_image_path_readback_failed",
        ));
    }
    let actual = OsString::from_wide(&words).to_string_lossy().into_owned();
    let actual = actual
        .strip_prefix(r"\\?\UNC\")
        .map(|value| format!(r"\\{value}"))
        .or_else(|| actual.strip_prefix(r"\\?\").map(str::to_string))
        .unwrap_or(actual);
    if !actual.eq_ignore_ascii_case(expected.to_string_lossy().as_ref()) {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_client_server_image_path_mismatch",
        ));
    }
    Ok(())
}

fn paths_equal(left: &Path, right: &Path) -> bool {
    left.to_string_lossy()
        .eq_ignore_ascii_case(right.to_string_lossy().as_ref())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn current_process_and_image_handles_produce_stable_candidate_evidence() {
        let process_id = unsafe { windows_sys::Win32::System::Threading::GetCurrentProcessId() };
        let raw = unsafe {
            OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE,
                0,
                process_id,
            )
        };
        assert!(!raw.is_null());
        let process = unsafe { OwnedHandle::from_raw_handle(raw as RawHandle) };
        let creation_time = process_creation_time(process.as_raw_handle().cast()).unwrap();
        let image = HeldServerImage::open(process.as_raw_handle().cast()).unwrap();
        let expected = CandidateProcessEvidence::from_held_process(
            process_id,
            creation_time,
            image.sha256,
            image.identity.byte_length,
            image.identity.volume_serial,
            image.identity.file_id,
            image.identity.link_count,
            image.identity.attributes,
        )
        .unwrap();
        drop(image);

        let mut held = HeldCandidateServer::open(process, expected).unwrap();
        held.revalidate(true).unwrap();
        assert_eq!(held.evidence().unwrap(), expected);
    }

    #[test]
    fn transcript_digest_binds_every_request_response_and_server_field() {
        let server = CandidateProcessEvidence::from_held_process(
            41, 42, [0x43; 32], 44, 45, [0x46; 16], 1, 0x20,
        )
        .unwrap();
        let baseline = transcript_digest(&[0x48; 16], &server, b"request", b"response");
        assert_ne!(
            baseline,
            transcript_digest(&[0x49; 16], &server, b"request", b"response")
        );
        assert_ne!(
            baseline,
            transcript_digest(&[0x48; 16], &server, b"request!", b"response")
        );
        assert_ne!(
            baseline,
            transcript_digest(&[0x48; 16], &server, b"request", b"response!")
        );
        let drift = CandidateProcessEvidence::from_held_process(
            41, 43, [0x43; 32], 44, 45, [0x46; 16], 1, 0x20,
        )
        .unwrap();
        assert_ne!(
            baseline,
            transcript_digest(&[0x48; 16], &drift, b"request", b"response")
        );
        let drift = CandidateProcessEvidence::from_held_process(
            41, 42, [0x43; 32], 44, 45, [0x47; 16], 1, 0x20,
        )
        .unwrap();
        assert_ne!(
            baseline,
            transcript_digest(&[0x48; 16], &drift, b"request", b"response")
        );
    }

    #[test]
    fn stopped_proof_binds_generation_process_image_and_zero_exit() {
        let server = CandidateProcessEvidence::from_held_process(
            51, 52, [0x53; 32], 54, 55, [0x56; 16], 1, 0x20,
        )
        .unwrap();
        let baseline = NativeCandidateStoppedProof {
            generation: [0x57; 32],
            server,
            process_exit_code: 0,
        }
        .digest()
        .unwrap();
        assert_ne!(
            baseline,
            NativeCandidateStoppedProof {
                generation: [0x58; 32],
                server,
                process_exit_code: 0,
            }
            .digest()
            .unwrap()
        );
        let drift = CandidateProcessEvidence::from_held_process(
            51, 53, [0x53; 32], 54, 55, [0x56; 16], 1, 0x20,
        )
        .unwrap();
        assert_ne!(
            baseline,
            NativeCandidateStoppedProof {
                generation: [0x57; 32],
                server: drift,
                process_exit_code: 0,
            }
            .digest()
            .unwrap()
        );
        assert_eq!(
            NativeCandidateStoppedProof {
                generation: [0x57; 32],
                server,
                process_exit_code: 1,
            }
            .digest()
            .unwrap_err()
            .code(),
            "authority_candidate_client_stopped_proof_invalid"
        );
    }
}

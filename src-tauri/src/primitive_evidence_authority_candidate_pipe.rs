use crate::primitive_evidence_authority_install::bootstrap::{
    candidate_pipe_name, CandidateProcessEvidence, CandidateValidationHandshake,
    CandidateValidationRequest, CANDIDATE_HANDSHAKE_WINDOW_MILLIS, MAX_CANDIDATE_HANDSHAKE_BYTES,
};
use sha2::{Digest, Sha256};
use std::{
    ffi::OsString,
    fmt,
    fs::{File, OpenOptions},
    io::{Read, Seek, SeekFrom},
    mem::{size_of, zeroed},
    os::windows::{
        ffi::{OsStrExt, OsStringExt},
        fs::{MetadataExt, OpenOptionsExt},
        io::{AsRawHandle, FromRawHandle, OwnedHandle, RawHandle},
    },
    path::{Path, PathBuf},
    ptr,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    time::{Duration, Instant},
};
use windows_sys::Win32::{
    Foundation::{
        CloseHandle, GetLastError, LocalFree, ERROR_BROKEN_PIPE, ERROR_INSUFFICIENT_BUFFER,
        ERROR_MORE_DATA, ERROR_OPERATION_ABORTED, ERROR_PIPE_CONNECTED, FILETIME, HANDLE,
        INVALID_HANDLE_VALUE, STILL_ACTIVE,
    },
    Security::{
        Authorization::{
            ConvertStringSecurityDescriptorToSecurityDescriptorW, ConvertStringSidToSidW,
            SDDL_REVISION_1,
        },
        CreateWellKnownSid, EqualSid, GetTokenInformation, IsValidSid, TokenRestrictedSids,
        TokenSessionId, TokenUser, WinLocalSystemSid, PSECURITY_DESCRIPTOR, PSID,
        SECURITY_ATTRIBUTES, TOKEN_GROUPS, TOKEN_QUERY, TOKEN_USER,
    },
    Storage::FileSystem::{
        FlushFileBuffers, GetFileInformationByHandle, GetFinalPathNameByHandleW, ReadFile,
        WriteFile, BY_HANDLE_FILE_INFORMATION, FILE_ATTRIBUTE_DIRECTORY,
        FILE_ATTRIBUTE_REPARSE_POINT, FILE_FLAG_FIRST_PIPE_INSTANCE, FILE_FLAG_OPEN_REPARSE_POINT,
        FILE_FLAG_SEQUENTIAL_SCAN, FILE_SHARE_DELETE, FILE_SHARE_READ, FILE_SHARE_WRITE,
        PIPE_ACCESS_DUPLEX,
    },
    System::{
        Pipes::{
            ConnectNamedPipe, CreateNamedPipeW, DisconnectNamedPipe, GetNamedPipeClientProcessId,
            PIPE_READMODE_MESSAGE, PIPE_REJECT_REMOTE_CLIENTS, PIPE_TYPE_MESSAGE, PIPE_WAIT,
        },
        Threading::{
            GetExitCodeProcess, GetProcessId, GetProcessTimes, OpenProcess, OpenProcessToken,
            QueryFullProcessImageNameW, PROCESS_QUERY_LIMITED_INFORMATION,
        },
        IO::CancelIoEx,
    },
};

const AUTHORITY_SERVICE_SID: &str = "S-1-5-80-627086344-872206109-3199044541-2745001037-75066892";
const MAINTENANCE_WORKER_SERVICE_SID: &str =
    "S-1-5-80-1152445285-3302248683-2168573404-3713171798-555061439";
const CANDIDATE_PIPE_SDDL: &str = "O:SYG:SYD:P(A;;GA;;;SY)(A;;GA;;;S-1-5-80-627086344-872206109-3199044541-2745001037-75066892)(A;;GA;;;S-1-5-80-1152445285-3302248683-2168573404-3713171798-555061439)S:(ML;;NW;;;HI)";
const CANDIDATE_PIPE_TIMEOUT: Duration =
    Duration::from_millis(CANDIDATE_HANDSHAKE_WINDOW_MILLIS as u64);
const PROCESS_SYNCHRONIZE: u32 = 0x0010_0000;
const MAX_PEER_IMAGE_BYTES: u64 = 512 * 1024 * 1024;

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct CandidatePipeError(&'static str);

impl CandidatePipeError {
    #[allow(dead_code)]
    pub(crate) fn code(&self) -> &'static str {
        self.0
    }
}

impl fmt::Display for CandidatePipeError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.0)
    }
}

impl std::error::Error for CandidatePipeError {}

/// An affine proof created only from this endpoint's authenticated, held pipe
/// client process and image handles. Its private field prevents a scalar
/// `CandidateProcessEvidence` from entering the credential-consumption lane.
pub(crate) struct CandidatePeerEvidence {
    process: CandidateProcessEvidence,
}

impl CandidatePeerEvidence {
    fn from_authenticated_process(
        process: CandidateProcessEvidence,
    ) -> Result<Self, CandidatePipeError> {
        process
            .validate()
            .map_err(|_| CandidatePipeError("authority_candidate_peer_evidence_invalid"))?;
        Ok(Self { process })
    }

    pub(super) fn into_verified_process_evidence(
        self,
    ) -> Result<CandidateProcessEvidence, CandidatePipeError> {
        self.process
            .validate()
            .map_err(|_| CandidatePipeError("authority_candidate_peer_evidence_invalid"))?;
        Ok(self.process)
    }
}

pub(crate) struct CandidateValidationEndpoint {
    handle: Arc<OwnedHandle>,
    pipe_name: String,
}

impl CandidateValidationEndpoint {
    pub(crate) fn prepare(pipe_instance_id: &[u8; 16]) -> Result<Self, CandidatePipeError> {
        let pipe_name = candidate_pipe_name(pipe_instance_id)
            .map_err(|_| CandidatePipeError("authority_candidate_pipe_instance_invalid"))?;
        let descriptor = SecurityDescriptor::from_sddl(CANDIDATE_PIPE_SDDL)?;
        let mut attributes = SECURITY_ATTRIBUTES {
            nLength: size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: descriptor.0,
            bInheritHandle: 0,
        };
        let encoded_name = wide_null(Path::new(&pipe_name));
        let handle = unsafe {
            CreateNamedPipeW(
                encoded_name.as_ptr(),
                PIPE_ACCESS_DUPLEX | FILE_FLAG_FIRST_PIPE_INSTANCE,
                PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
                1,
                MAX_CANDIDATE_HANDSHAKE_BYTES as u32,
                MAX_CANDIDATE_HANDSHAKE_BYTES as u32,
                CANDIDATE_PIPE_TIMEOUT.as_millis() as u32,
                &mut attributes,
            )
        };
        if handle == INVALID_HANDLE_VALUE {
            return Err(CandidatePipeError("authority_candidate_pipe_create_failed"));
        }
        Ok(Self {
            handle: Arc::new(unsafe { OwnedHandle::from_raw_handle(handle as RawHandle) }),
            pipe_name,
        })
    }

    pub(crate) fn pipe_name(&self) -> &str {
        &self.pipe_name
    }

    /// Accepts exactly one LocalSystem peer, exactly one canonical request, and
    /// exactly one canonical response. No runtime command framing is present.
    pub(crate) fn serve_one<F>(
        self,
        stop_requested: fn() -> bool,
        complete: F,
    ) -> Result<CandidateValidationHandshake, CandidatePipeError>
    where
        F: FnOnce(
            CandidateValidationRequest,
            CandidatePeerEvidence,
        ) -> Result<CandidateValidationHandshake, &'static str>,
    {
        let done = Arc::new(AtomicBool::new(false));
        let timed_out = Arc::new(AtomicBool::new(false));
        let watcher_handle = Arc::clone(&self.handle);
        let watcher_done = Arc::clone(&done);
        let watcher_timeout = Arc::clone(&timed_out);
        let deadline = Instant::now() + CANDIDATE_PIPE_TIMEOUT;
        let watcher = std::thread::spawn(move || {
            while !watcher_done.load(Ordering::Acquire) {
                if stop_requested() || Instant::now() >= deadline {
                    if Instant::now() >= deadline {
                        watcher_timeout.store(true, Ordering::Release);
                    }
                    cancel_and_disconnect(raw_handle(&watcher_handle));
                    return;
                }
                std::thread::sleep(Duration::from_millis(10));
            }
        });

        let result = self.exchange_once(complete);
        done.store(true, Ordering::Release);
        if watcher.join().is_err() {
            return Err(CandidatePipeError(
                "authority_candidate_pipe_watcher_failed",
            ));
        }
        if timed_out.load(Ordering::Acquire) {
            return Err(CandidatePipeError("authority_candidate_pipe_timeout"));
        }
        result
    }

    fn exchange_once<F>(
        &self,
        complete: F,
    ) -> Result<CandidateValidationHandshake, CandidatePipeError>
    where
        F: FnOnce(
            CandidateValidationRequest,
            CandidatePeerEvidence,
        ) -> Result<CandidateValidationHandshake, &'static str>,
    {
        let handle = raw_handle(&self.handle);
        let connected = unsafe { ConnectNamedPipe(handle, ptr::null_mut()) };
        if connected == 0 {
            let error = unsafe { GetLastError() };
            if error != ERROR_PIPE_CONNECTED {
                return Err(CandidatePipeError(if error == ERROR_OPERATION_ABORTED {
                    "authority_candidate_pipe_cancelled"
                } else {
                    "authority_candidate_pipe_connect_failed"
                }));
            }
        }
        let mut peer = CandidatePeer::authenticate(handle)?;
        let request_bytes = read_one_message(handle)?;
        let request = CandidateValidationRequest::parse_canonical(&request_bytes)
            .map_err(|_| CandidatePipeError("authority_candidate_pipe_request_invalid"))?;
        peer.revalidate()?;
        let peer_evidence = peer.evidence()?;
        let handshake = complete(request, peer_evidence).map_err(CandidatePipeError)?;
        let response = handshake
            .canonical_bytes()
            .map_err(|_| CandidatePipeError("authority_candidate_pipe_response_invalid"))?;
        write_one_message(handle, &response)?;
        peer.revalidate()?;
        Ok(handshake)
    }
}

impl Drop for CandidateValidationEndpoint {
    fn drop(&mut self) {
        unsafe {
            DisconnectNamedPipe(raw_handle(&self.handle));
        }
    }
}

struct CandidatePeer {
    process: OwnedHandle,
    process_id: u32,
    process_creation_time: u64,
    image: HeldPeerImage,
}

impl CandidatePeer {
    fn authenticate(pipe: HANDLE) -> Result<Self, CandidatePipeError> {
        let mut process_id = 0u32;
        if unsafe { GetNamedPipeClientProcessId(pipe, &mut process_id) } == 0 || process_id == 0 {
            return Err(CandidatePipeError(
                "authority_candidate_peer_identity_unavailable",
            ));
        }
        let process = unsafe {
            OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SYNCHRONIZE,
                0,
                process_id,
            )
        };
        if process.is_null() {
            return Err(CandidatePipeError(
                "authority_candidate_peer_process_unavailable",
            ));
        }
        let process = unsafe { OwnedHandle::from_raw_handle(process as RawHandle) };
        let process_creation_time = query_process_creation_time(raw_owned(&process))?;
        require_local_system_session_zero(raw_owned(&process))?;
        let image = HeldPeerImage::open(raw_owned(&process))?;
        let mut peer = Self {
            process,
            process_id,
            process_creation_time,
            image,
        };
        peer.revalidate()?;
        Ok(peer)
    }

    fn revalidate(&mut self) -> Result<(), CandidatePipeError> {
        let process = raw_owned(&self.process);
        let mut exit_code = 0u32;
        if unsafe { GetProcessId(process) } != self.process_id
            || query_process_creation_time(process)? != self.process_creation_time
            || unsafe { GetExitCodeProcess(process, &mut exit_code) } == 0
            || exit_code != STILL_ACTIVE as u32
        {
            return Err(CandidatePipeError(
                "authority_candidate_peer_identity_changed",
            ));
        }
        require_local_system_session_zero(process)?;
        self.image.verify(process)
    }

    fn evidence(&self) -> Result<CandidatePeerEvidence, CandidatePipeError> {
        let process = CandidateProcessEvidence::from_held_process(
            self.process_id,
            self.process_creation_time,
            self.image.sha256,
            self.image.identity.byte_length,
            self.image.identity.volume_serial,
            self.image.identity.file_id,
            self.image.identity.link_count,
            self.image.identity.attributes,
        )
        .map_err(|_| CandidatePipeError("authority_candidate_peer_evidence_invalid"))?;
        CandidatePeerEvidence::from_authenticated_process(process)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct PeerImageIdentity {
    volume_serial: u64,
    file_id: [u8; 16],
    byte_length: u64,
    link_count: u32,
    attributes: u32,
}

struct HeldPeerImage {
    file: File,
    path: PathBuf,
    identity: PeerImageIdentity,
    sha256: [u8; 32],
}

impl HeldPeerImage {
    fn open(process: HANDLE) -> Result<Self, CandidatePipeError> {
        let path = process_image_path(process)?;
        let metadata = std::fs::symlink_metadata(&path)
            .map_err(|_| CandidatePipeError("authority_candidate_peer_image_metadata_failed"))?;
        if !metadata.is_file()
            || metadata.file_type().is_symlink()
            || metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
            || metadata.len() == 0
            || metadata.len() > MAX_PEER_IMAGE_BYTES
        {
            return Err(CandidatePipeError(
                "authority_candidate_peer_image_metadata_invalid",
            ));
        }
        let mut file = OpenOptions::new()
            .read(true)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
            .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_SEQUENTIAL_SCAN)
            .open(&path)
            .map_err(|_| CandidatePipeError("authority_candidate_peer_image_open_failed"))?;
        require_exact_handle_path(&file, &path)?;
        let (identity, sha256) = read_peer_image(&mut file)?;
        if identity.link_count != 1
            || identity.attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT) != 0
            || identity.byte_length != metadata.len()
        {
            return Err(CandidatePipeError(
                "authority_candidate_peer_image_identity_invalid",
            ));
        }
        Ok(Self {
            file,
            path,
            identity,
            sha256,
        })
    }

    fn verify(&mut self, process: HANDLE) -> Result<(), CandidatePipeError> {
        if !paths_equal(&process_image_path(process)?, &self.path) {
            return Err(CandidatePipeError(
                "authority_candidate_peer_image_path_changed",
            ));
        }
        require_exact_handle_path(&self.file, &self.path)?;
        let (identity, sha256) = read_peer_image(&mut self.file)?;
        if identity != self.identity || sha256 != self.sha256 {
            return Err(CandidatePipeError(
                "authority_candidate_peer_image_identity_changed",
            ));
        }
        Ok(())
    }
}

fn read_one_message(handle: HANDLE) -> Result<Vec<u8>, CandidatePipeError> {
    let mut buffer = vec![0u8; MAX_CANDIDATE_HANDSHAKE_BYTES + 1];
    let mut read = 0u32;
    if unsafe {
        ReadFile(
            handle,
            buffer.as_mut_ptr().cast(),
            buffer.len() as u32,
            &mut read,
            ptr::null_mut(),
        )
    } == 0
    {
        let error = unsafe { GetLastError() };
        return Err(CandidatePipeError(if error == ERROR_MORE_DATA {
            "authority_candidate_pipe_request_too_large"
        } else if matches!(error, ERROR_OPERATION_ABORTED | ERROR_BROKEN_PIPE) {
            "authority_candidate_pipe_cancelled"
        } else {
            "authority_candidate_pipe_read_failed"
        }));
    }
    let read = read as usize;
    if read == 0 || read > MAX_CANDIDATE_HANDSHAKE_BYTES {
        return Err(CandidatePipeError(
            "authority_candidate_pipe_request_size_invalid",
        ));
    }
    buffer.truncate(read);
    Ok(buffer)
}

fn write_one_message(handle: HANDLE, bytes: &[u8]) -> Result<(), CandidatePipeError> {
    if bytes.is_empty() || bytes.len() > MAX_CANDIDATE_HANDSHAKE_BYTES {
        return Err(CandidatePipeError(
            "authority_candidate_pipe_response_size_invalid",
        ));
    }
    let mut written = 0u32;
    if unsafe {
        WriteFile(
            handle,
            bytes.as_ptr().cast(),
            bytes.len() as u32,
            &mut written,
            ptr::null_mut(),
        )
    } == 0
        || written as usize != bytes.len()
    {
        return Err(CandidatePipeError("authority_candidate_pipe_write_failed"));
    }
    if unsafe { FlushFileBuffers(handle) } == 0 {
        return Err(CandidatePipeError("authority_candidate_pipe_flush_failed"));
    }
    Ok(())
}

fn require_local_system_session_zero(process: HANDLE) -> Result<(), CandidatePipeError> {
    let mut raw_token: HANDLE = ptr::null_mut();
    if unsafe { OpenProcessToken(process, TOKEN_QUERY, &mut raw_token) } == 0 || raw_token.is_null()
    {
        return Err(CandidatePipeError(
            "authority_candidate_peer_token_unavailable",
        ));
    }
    struct Token(HANDLE);
    impl Drop for Token {
        fn drop(&mut self) {
            if !self.0.is_null() {
                unsafe {
                    CloseHandle(self.0);
                }
            }
        }
    }
    let token = Token(raw_token);
    let session_id: u32 = query_token_fixed(token.0, TokenSessionId)?;
    let user = query_token_buffer(token.0, TokenUser)?;
    let token_user = unsafe { &*(user.as_ptr().cast::<TOKEN_USER>()) };
    if token_user.User.Sid.is_null() || unsafe { IsValidSid(token_user.User.Sid) } == 0 {
        return Err(CandidatePipeError("authority_candidate_peer_token_invalid"));
    }
    let mut system_sid = [0usize; 9];
    let mut system_sid_size = (system_sid.len() * size_of::<usize>()) as u32;
    if unsafe {
        CreateWellKnownSid(
            WinLocalSystemSid,
            ptr::null_mut(),
            system_sid.as_mut_ptr().cast(),
            &mut system_sid_size,
        )
    } == 0
        || unsafe { IsValidSid(system_sid.as_ptr().cast_mut().cast()) } == 0
        || unsafe { EqualSid(token_user.User.Sid, system_sid.as_mut_ptr().cast()) } == 0
        || session_id != 0
    {
        return Err(CandidatePipeError(
            "authority_candidate_peer_system_identity_required",
        ));
    }
    require_exact_restricted_service_sid(token.0)
}

fn require_exact_restricted_service_sid(token: HANDLE) -> Result<(), CandidatePipeError> {
    let groups_buffer = query_token_buffer(token, TokenRestrictedSids)?;
    let buffer_bytes =
        groups_buffer
            .len()
            .checked_mul(size_of::<usize>())
            .ok_or(CandidatePipeError(
                "authority_candidate_peer_restricted_sid_invalid",
            ))?;
    if buffer_bytes < size_of::<TOKEN_GROUPS>() {
        return Err(CandidatePipeError(
            "authority_candidate_peer_restricted_sid_invalid",
        ));
    }
    let groups = unsafe { &*(groups_buffer.as_ptr().cast::<TOKEN_GROUPS>()) };
    let count = groups.GroupCount as usize;
    if count == 0 || count > 1024 {
        return Err(CandidatePipeError(
            "authority_candidate_peer_restricted_sid_invalid",
        ));
    }
    let groups_offset = (ptr::addr_of!(groups.Groups) as usize)
        .checked_sub(groups as *const TOKEN_GROUPS as usize)
        .ok_or(CandidatePipeError(
            "authority_candidate_peer_restricted_sid_invalid",
        ))?;
    let required = groups_offset
        .checked_add(
            count
                .checked_mul(size_of::<windows_sys::Win32::Security::SID_AND_ATTRIBUTES>())
                .ok_or(CandidatePipeError(
                    "authority_candidate_peer_restricted_sid_invalid",
                ))?,
        )
        .ok_or(CandidatePipeError(
            "authority_candidate_peer_restricted_sid_invalid",
        ))?;
    if required > buffer_bytes {
        return Err(CandidatePipeError(
            "authority_candidate_peer_restricted_sid_invalid",
        ));
    }
    let expected = OwnedSid::from_text(MAINTENANCE_WORKER_SERVICE_SID)?;
    let entries = unsafe { std::slice::from_raw_parts(groups.Groups.as_ptr(), count) };
    let matches = entries
        .iter()
        .filter(|entry| {
            !entry.Sid.is_null()
                && unsafe { IsValidSid(entry.Sid) } != 0
                && unsafe { EqualSid(entry.Sid, expected.raw()) } != 0
        })
        .count();
    if matches != 1 {
        return Err(CandidatePipeError(
            "authority_candidate_peer_service_sid_required",
        ));
    }
    Ok(())
}

fn query_token_fixed<T: Copy>(token: HANDLE, class: i32) -> Result<T, CandidatePipeError> {
    let mut value = std::mem::MaybeUninit::<T>::zeroed();
    let mut required = 0u32;
    if unsafe {
        GetTokenInformation(
            token,
            class,
            value.as_mut_ptr().cast(),
            size_of::<T>() as u32,
            &mut required,
        )
    } == 0
        || required as usize != size_of::<T>()
    {
        return Err(CandidatePipeError(
            "authority_candidate_peer_token_unavailable",
        ));
    }
    Ok(unsafe { value.assume_init() })
}

fn query_token_buffer(token: HANDLE, class: i32) -> Result<Vec<usize>, CandidatePipeError> {
    let mut required = 0u32;
    unsafe {
        GetTokenInformation(token, class, ptr::null_mut(), 0, &mut required);
    }
    if required == 0
        || required > 64 * 1024
        || unsafe { GetLastError() } != ERROR_INSUFFICIENT_BUFFER
    {
        return Err(CandidatePipeError(
            "authority_candidate_peer_token_unavailable",
        ));
    }
    let word_size = size_of::<usize>();
    let word_count = (required as usize)
        .checked_add(word_size - 1)
        .ok_or(CandidatePipeError(
            "authority_candidate_peer_token_unavailable",
        ))?
        / word_size;
    let mut buffer = vec![0usize; word_count];
    let mut written = 0u32;
    if unsafe {
        GetTokenInformation(
            token,
            class,
            buffer.as_mut_ptr().cast(),
            required,
            &mut written,
        )
    } == 0
        || written != required
    {
        return Err(CandidatePipeError(
            "authority_candidate_peer_token_unavailable",
        ));
    }
    Ok(buffer)
}

fn query_process_creation_time(process: HANDLE) -> Result<u64, CandidatePipeError> {
    let mut creation: FILETIME = unsafe { zeroed() };
    let mut exit: FILETIME = unsafe { zeroed() };
    let mut kernel: FILETIME = unsafe { zeroed() };
    let mut user: FILETIME = unsafe { zeroed() };
    if unsafe { GetProcessTimes(process, &mut creation, &mut exit, &mut kernel, &mut user) } == 0 {
        return Err(CandidatePipeError(
            "authority_candidate_peer_process_times_unavailable",
        ));
    }
    let value = (u64::from(creation.dwHighDateTime) << 32) | u64::from(creation.dwLowDateTime);
    if value == 0 {
        return Err(CandidatePipeError(
            "authority_candidate_peer_process_times_invalid",
        ));
    }
    Ok(value)
}

fn process_image_path(process: HANDLE) -> Result<PathBuf, CandidatePipeError> {
    let mut words = vec![0u16; 32_768];
    let mut length = words.len() as u32;
    if unsafe { QueryFullProcessImageNameW(process, 0, words.as_mut_ptr(), &mut length) } == 0
        || length == 0
        || length as usize >= words.len()
    {
        return Err(CandidatePipeError(
            "authority_candidate_peer_image_path_unavailable",
        ));
    }
    words.truncate(length as usize);
    if words.contains(&0) {
        return Err(CandidatePipeError(
            "authority_candidate_peer_image_path_unavailable",
        ));
    }
    Ok(PathBuf::from(OsString::from_wide(&words)))
}

fn read_peer_image(file: &mut File) -> Result<(PeerImageIdentity, [u8; 32]), CandidatePipeError> {
    let before = peer_image_identity(file)?;
    if before.byte_length == 0 || before.byte_length > MAX_PEER_IMAGE_BYTES {
        return Err(CandidatePipeError(
            "authority_candidate_peer_image_size_invalid",
        ));
    }
    file.seek(SeekFrom::Start(0))
        .map_err(|_| CandidatePipeError("authority_candidate_peer_image_read_failed"))?;
    let mut digest = Sha256::new();
    let mut buffer = [0u8; 64 * 1024];
    let mut length = 0u64;
    loop {
        let count = file
            .read(&mut buffer)
            .map_err(|_| CandidatePipeError("authority_candidate_peer_image_read_failed"))?;
        if count == 0 {
            break;
        }
        length = length.checked_add(count as u64).ok_or(CandidatePipeError(
            "authority_candidate_peer_image_size_invalid",
        ))?;
        if length > MAX_PEER_IMAGE_BYTES {
            return Err(CandidatePipeError(
                "authority_candidate_peer_image_size_invalid",
            ));
        }
        digest.update(&buffer[..count]);
    }
    file.seek(SeekFrom::Start(0))
        .map_err(|_| CandidatePipeError("authority_candidate_peer_image_read_failed"))?;
    let after = peer_image_identity(file)?;
    if before != after || length != before.byte_length {
        return Err(CandidatePipeError(
            "authority_candidate_peer_image_identity_changed",
        ));
    }
    Ok((before, digest.finalize().into()))
}

fn peer_image_identity(file: &File) -> Result<PeerImageIdentity, CandidatePipeError> {
    let mut information: BY_HANDLE_FILE_INFORMATION = unsafe { zeroed() };
    if unsafe { GetFileInformationByHandle(file.as_raw_handle().cast(), &mut information) } == 0 {
        return Err(CandidatePipeError(
            "authority_candidate_peer_image_identity_unavailable",
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
        return Err(CandidatePipeError(
            "authority_candidate_peer_image_identity_invalid",
        ));
    }
    Ok(PeerImageIdentity {
        volume_serial,
        file_id,
        byte_length,
        link_count: information.nNumberOfLinks,
        attributes: information.dwFileAttributes,
    })
}

fn require_exact_handle_path(file: &File, expected: &Path) -> Result<(), CandidatePipeError> {
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
        return Err(CandidatePipeError(
            "authority_candidate_peer_image_path_readback_failed",
        ));
    }
    words.truncate(length);
    if words.contains(&0) {
        return Err(CandidatePipeError(
            "authority_candidate_peer_image_path_readback_failed",
        ));
    }
    let actual = OsString::from_wide(&words).to_string_lossy().into_owned();
    let actual = actual
        .strip_prefix(r"\\?\UNC\")
        .map(|value| format!(r"\\{value}"))
        .or_else(|| actual.strip_prefix(r"\\?\").map(str::to_string))
        .unwrap_or(actual);
    if !actual.eq_ignore_ascii_case(expected.to_string_lossy().as_ref()) {
        return Err(CandidatePipeError(
            "authority_candidate_peer_image_path_mismatch",
        ));
    }
    Ok(())
}

fn paths_equal(left: &Path, right: &Path) -> bool {
    left.to_string_lossy()
        .eq_ignore_ascii_case(right.to_string_lossy().as_ref())
}

fn cancel_and_disconnect(handle: HANDLE) {
    unsafe {
        CancelIoEx(handle, ptr::null());
        DisconnectNamedPipe(handle);
    }
}

fn raw_handle(handle: &Arc<OwnedHandle>) -> HANDLE {
    handle.as_ref().as_raw_handle().cast()
}

fn raw_owned(handle: &OwnedHandle) -> HANDLE {
    handle.as_raw_handle().cast()
}

fn wide_null(path: &Path) -> Vec<u16> {
    path.as_os_str()
        .encode_wide()
        .chain(std::iter::once(0))
        .collect()
}

struct SecurityDescriptor(PSECURITY_DESCRIPTOR);

impl SecurityDescriptor {
    fn from_sddl(value: &str) -> Result<Self, CandidatePipeError> {
        let encoded = wide_null(Path::new(value));
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
            return Err(CandidatePipeError("authority_candidate_pipe_sddl_invalid"));
        }
        Ok(Self(descriptor))
    }
}

struct OwnedSid(PSID);

impl OwnedSid {
    fn from_text(value: &str) -> Result<Self, CandidatePipeError> {
        if value.is_empty() || value.contains('\0') {
            return Err(CandidatePipeError(
                "authority_candidate_peer_service_sid_invalid",
            ));
        }
        let encoded = value
            .encode_utf16()
            .chain(std::iter::once(0))
            .collect::<Vec<_>>();
        let mut sid = ptr::null_mut();
        if unsafe { ConvertStringSidToSidW(encoded.as_ptr(), &mut sid) } == 0
            || sid.is_null()
            || unsafe { IsValidSid(sid) } == 0
        {
            if !sid.is_null() {
                unsafe {
                    LocalFree(sid);
                }
            }
            return Err(CandidatePipeError(
                "authority_candidate_peer_service_sid_invalid",
            ));
        }
        Ok(Self(sid))
    }

    fn raw(&self) -> PSID {
        self.0
    }
}

impl Drop for OwnedSid {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe {
                LocalFree(self.0);
            }
            self.0 = ptr::null_mut();
        }
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn candidate_pipe_policy_is_narrow_and_parseable() {
        assert_eq!(
            CANDIDATE_PIPE_TIMEOUT.as_millis(),
            u128::from(CANDIDATE_HANDSHAKE_WINDOW_MILLIS)
        );
        assert!(CANDIDATE_PIPE_SDDL.contains(";;;SY)"));
        assert!(CANDIDATE_PIPE_SDDL.contains(AUTHORITY_SERVICE_SID));
        assert!(CANDIDATE_PIPE_SDDL.contains(MAINTENANCE_WORKER_SERVICE_SID));
        assert!(!CANDIDATE_PIPE_SDDL.contains(";;;BA)"));
        assert!(!CANDIDATE_PIPE_SDDL.contains(";;;BU)"));
        SecurityDescriptor::from_sddl(CANDIDATE_PIPE_SDDL).unwrap();
    }

    #[test]
    fn peer_evidence_is_an_affine_wrapper_not_a_process_evidence_alias() {
        let source = include_str!("primitive_evidence_authority_candidate_pipe.rs");
        let production = source.split("\n#[cfg(test)]").next().unwrap();
        assert!(production.contains("pub(crate) struct CandidatePeerEvidence"));
        assert!(!production.contains("type CandidatePeerEvidence = CandidateProcessEvidence"));
        assert!(production.contains("fn into_verified_process_evidence(\n        self,"));
        assert!(!production.contains("pub(crate) process: CandidateProcessEvidence"));
    }
}

use std::{
    fmt,
    path::{Component, Path, PathBuf},
};

use crate::primitive_evidence_authority_windows::{
    AuthorityLayout, AUTHORITY_PIPE_NAME, AUTHORITY_PIPE_SDDL,
};

const PIPE_BUFFER_BYTES: u32 = 64 * 1024;
const PIPE_DEFAULT_TIMEOUT_MS: u32 = 5_000;
const MAX_CONTROLLER_BYTES: u64 = 256 * 1024 * 1024;

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

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthorityPeerPolicy {
    expected_controller_path: PathBuf,
    expected_controller_sha256: [u8; 32],
    expected_session_id: u32,
}

impl AuthorityPeerPolicy {
    pub fn for_installed_layout(
        layout: &AuthorityLayout,
        expected_controller_sha256: [u8; 32],
        expected_session_id: u32,
    ) -> Result<Self, AuthorityPipeError> {
        let path = layout
            .controller_executable_for_digest(&expected_controller_sha256)
            .map_err(|_| AuthorityPipeError::new("authority_peer_controller_layout_invalid"))?;
        Self::new(path, expected_controller_sha256, expected_session_id)
    }

    fn new(
        expected_controller_path: PathBuf,
        expected_controller_sha256: [u8; 32],
        expected_session_id: u32,
    ) -> Result<Self, AuthorityPipeError> {
        if !expected_controller_path.is_absolute()
            || expected_controller_path.as_os_str().is_empty()
            || expected_controller_path
                .components()
                .any(|component| matches!(component, Component::CurDir | Component::ParentDir))
        {
            return Err(AuthorityPipeError::new(
                "authority_peer_controller_path_invalid",
            ));
        }
        let expected_digest_component = hex_lower(&expected_controller_sha256);
        if expected_controller_sha256.iter().all(|byte| *byte == 0)
            || expected_controller_path
                .parent()
                .and_then(Path::file_name)
                .and_then(|value| value.to_str())
                != Some(expected_digest_component.as_str())
        {
            return Err(AuthorityPipeError::new(
                "authority_peer_controller_path_not_content_addressed",
            ));
        }
        Ok(Self {
            expected_controller_path,
            expected_controller_sha256,
            expected_session_id,
        })
    }

    pub fn expected_controller_path(&self) -> &Path {
        &self.expected_controller_path
    }

    pub fn expected_controller_sha256(&self) -> &[u8; 32] {
        &self.expected_controller_sha256
    }

    pub fn expected_session_id(&self) -> u32 {
        self.expected_session_id
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
    pub controller_path: &'a Path,
    pub controller_sha256: [u8; 32],
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
    if facts.controller_sha256 != policy.expected_controller_sha256 {
        return Err(AuthorityPipeError::new(
            "authority_peer_controller_digest_mismatch",
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
        || facts.pipe_session_id != policy.expected_session_id
    {
        return Err(AuthorityPipeError::new("authority_peer_session_mismatch"));
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
    use sha2::{Digest, Sha256};
    use std::{
        fs::File,
        io::{Read, Seek, SeekFrom},
        mem::{size_of, zeroed},
        os::windows::{
            ffi::OsStrExt,
            io::{AsHandle, AsRawHandle, BorrowedHandle, FromRawHandle, OwnedHandle, RawHandle},
        },
        ptr,
    };
    use windows_sys::Win32::{
        Foundation::{
            GetLastError, LocalFree, ERROR_INSUFFICIENT_BUFFER, ERROR_PIPE_CONNECTED, FILETIME,
            GENERIC_READ, GENERIC_WRITE, INVALID_HANDLE_VALUE, WAIT_FAILED, WAIT_OBJECT_0,
            WAIT_TIMEOUT,
        },
        Security::{
            Authorization::{
                ConvertStringSecurityDescriptorToSecurityDescriptorW, SDDL_REVISION_1,
            },
            CheckTokenMembership, CreateWellKnownSid, GetSidSubAuthority, GetSidSubAuthorityCount,
            GetTokenInformation, IsValidSid, TokenElevation, TokenIntegrityLevel, TokenSessionId,
            WinBuiltinAdministratorsSid, SECURITY_ATTRIBUTES, SECURITY_MAX_SID_SIZE,
            TOKEN_ELEVATION, TOKEN_MANDATORY_LABEL, TOKEN_QUERY,
        },
        Storage::FileSystem::{
            CreateFileW, GetFileInformationByHandle, BY_HANDLE_FILE_INFORMATION,
            FILE_ATTRIBUTE_DIRECTORY, FILE_ATTRIBUTE_REPARSE_POINT, FILE_FLAG_FIRST_PIPE_INSTANCE,
            FILE_FLAG_OPEN_REPARSE_POINT, FILE_FLAG_SEQUENTIAL_SCAN, FILE_READ_ATTRIBUTES,
            FILE_READ_DATA, FILE_SHARE_READ, OPEN_EXISTING, PIPE_ACCESS_DUPLEX,
        },
        System::{
            Pipes::{
                ConnectNamedPipe, CreateNamedPipeW, DisconnectNamedPipe,
                GetNamedPipeClientProcessId, GetNamedPipeClientSessionId, PIPE_READMODE_MESSAGE,
                PIPE_REJECT_REMOTE_CLIENTS, PIPE_TYPE_MESSAGE, PIPE_WAIT,
            },
            SystemServices::SECURITY_MANDATORY_HIGH_RID,
            Threading::{
                GetCurrentProcess, GetProcessTimes, OpenProcess, OpenProcessToken,
                QueryFullProcessImageNameW, WaitForSingleObject, PROCESS_QUERY_LIMITED_INFORMATION,
            },
        },
    };

    const SYNCHRONIZE_ACCESS: u32 = 0x0010_0000;
    const TEST_PIPE_SDDL: &str = "D:P(A;;GA;;;WD)";

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

    pub struct AuthorityPeerIdentity {
        process_id: u32,
        session_id: u32,
        process_creation_time: u64,
        controller_path: PathBuf,
        controller_sha256: [u8; 32],
        controller_file_identity: StableFileIdentity,
        process_handle: OwnedHandle,
        controller_file: File,
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

        pub fn process_handle(&self) -> BorrowedHandle<'_> {
            self.process_handle.as_handle()
        }

        pub fn controller_file_handle(&self) -> BorrowedHandle<'_> {
            self.controller_file.as_handle()
        }
    }

    #[derive(Debug)]
    pub struct AuthorityPipe {
        handle: OwnedHandle,
    }

    impl AuthorityPipe {
        pub fn create() -> Result<Self, AuthorityPipeError> {
            create_pipe_with_sddl(AUTHORITY_PIPE_NAME, AUTHORITY_PIPE_SDDL)
        }

        pub fn accept_peer(
            &self,
            policy: &AuthorityPeerPolicy,
        ) -> Result<AuthorityPeerIdentity, AuthorityPipeError> {
            let connected = unsafe { ConnectNamedPipe(self.raw(), ptr::null_mut()) };
            if connected == 0 && unsafe { GetLastError() } != ERROR_PIPE_CONNECTED {
                return Err(AuthorityPipeError::last_win32(
                    "authority_pipe_connect_failed",
                ));
            }
            match authenticate_connected_peer(self.raw(), policy) {
                Ok(identity) => Ok(identity),
                Err(error) => {
                    unsafe {
                        DisconnectNamedPipe(self.raw());
                    }
                    Err(error)
                }
            }
        }

        pub fn handle(&self) -> BorrowedHandle<'_> {
            self.handle.as_handle()
        }

        fn raw(&self) -> windows_sys::Win32::Foundation::HANDLE {
            self.handle.as_raw_handle().cast()
        }
    }

    impl Drop for AuthorityPipe {
        fn drop(&mut self) {
            unsafe {
                DisconnectNamedPipe(self.raw());
            }
        }
    }

    struct SecurityDescriptor(*mut core::ffi::c_void);

    impl SecurityDescriptor {
        fn from_sddl(sddl: &str) -> Result<Self, AuthorityPipeError> {
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
    }

    fn create_pipe_with_sddl(
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
                PIPE_ACCESS_DUPLEX | FILE_FLAG_FIRST_PIPE_INSTANCE,
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
            handle: unsafe { OwnedHandle::from_raw_handle(handle as RawHandle) },
        })
    }

    fn authenticate_connected_peer(
        pipe: windows_sys::Win32::Foundation::HANDLE,
        policy: &AuthorityPeerPolicy,
    ) -> Result<AuthorityPeerIdentity, AuthorityPipeError> {
        let mut process_id = 0u32;
        if unsafe { GetNamedPipeClientProcessId(pipe, &mut process_id) } == 0 || process_id == 0 {
            return Err(AuthorityPipeError::last_win32(
                "authority_peer_process_id_unavailable",
            ));
        }
        let mut pipe_session_id = 0u32;
        if unsafe { GetNamedPipeClientSessionId(pipe, &mut pipe_session_id) } == 0 {
            return Err(AuthorityPipeError::last_win32(
                "authority_peer_session_unavailable",
            ));
        }
        let process = unsafe {
            OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE_ACCESS,
                0,
                process_id,
            )
        };
        if process.is_null() {
            return Err(AuthorityPipeError::last_win32(
                "authority_peer_process_open_failed",
            ));
        }
        let process_handle = unsafe { OwnedHandle::from_raw_handle(process as RawHandle) };
        let process_creation_time = query_process_creation_time(process)?;
        let controller_path = query_process_path(process)?;

        let token_snapshot = query_process_token(process)?;

        let pre_hash_facts = AuthorityPeerFacts {
            controller_path: &controller_path,
            controller_sha256: policy.expected_controller_sha256,
            pipe_session_id,
            token_session_id: token_snapshot.session_id,
            elevated: token_snapshot.elevated,
            high_integrity: token_snapshot.high_integrity,
            administrators_member: token_snapshot.administrators_member,
        };
        evaluate_peer_pre_hash_policy(policy, &pre_hash_facts)?;

        let (mut controller_file, controller_sha256, controller_file_identity) =
            open_and_hash_controller(policy.expected_controller_path())?;
        let controller_path_after_hash = query_process_path(process)?;
        if controller_path_after_hash != controller_path || !process_is_active(process)? {
            return Err(AuthorityPipeError::new(
                "authority_peer_process_identity_changed",
            ));
        }
        let facts = AuthorityPeerFacts {
            controller_path: &controller_path,
            controller_sha256,
            pipe_session_id,
            token_session_id: token_snapshot.session_id,
            elevated: token_snapshot.elevated,
            high_integrity: token_snapshot.high_integrity,
            administrators_member: token_snapshot.administrators_member,
        };
        evaluate_peer_policy(policy, &facts)?;
        controller_file.seek(SeekFrom::Start(0)).map_err(|error| {
            AuthorityPipeError::from_io("authority_peer_controller_rewind_failed", &error)
        })?;
        Ok(AuthorityPeerIdentity {
            process_id,
            session_id: pipe_session_id,
            process_creation_time,
            controller_path,
            controller_sha256,
            controller_file_identity,
            process_handle,
            controller_file,
        })
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

    fn process_is_active(
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
        {
            return Err(AuthorityPipeError::last_win32(
                "authority_peer_administrator_sid_unavailable",
            ));
        }
        let mut administrators_member = 0;
        if unsafe {
            CheckTokenMembership(
                token,
                administrator_sid.as_mut_ptr().cast(),
                &mut administrators_member,
            )
        } == 0
        {
            return Err(AuthorityPipeError::last_win32(
                "authority_peer_administrator_check_failed",
            ));
        }
        Ok(TokenSnapshot {
            session_id,
            elevated: elevation.TokenIsElevated != 0,
            high_integrity,
            administrators_member: administrators_member != 0,
        })
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

    fn open_and_hash_controller(
        path: &Path,
    ) -> Result<(File, [u8; 32], StableFileIdentity), AuthorityPipeError> {
        let encoded = wide_null(path.as_os_str());
        let handle = unsafe {
            CreateFileW(
                encoded.as_ptr(),
                FILE_READ_DATA | FILE_READ_ATTRIBUTES,
                FILE_SHARE_READ,
                ptr::null(),
                OPEN_EXISTING,
                FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_SEQUENTIAL_SCAN,
                ptr::null_mut(),
            )
        };
        if handle == INVALID_HANDLE_VALUE {
            return Err(AuthorityPipeError::last_win32(
                "authority_peer_controller_open_failed",
            ));
        }
        let mut file = unsafe { File::from_raw_handle(handle as RawHandle) };
        let before = query_file_identity(file.as_raw_handle().cast())?;
        if before.size == 0 || before.size > MAX_CONTROLLER_BYTES {
            return Err(AuthorityPipeError::new(
                "authority_peer_controller_size_invalid",
            ));
        }
        let mut hasher = Sha256::new();
        let mut buffer = [0u8; 64 * 1024];
        loop {
            let read = file.read(&mut buffer).map_err(|error| {
                AuthorityPipeError::from_io("authority_peer_controller_read_failed", &error)
            })?;
            if read == 0 {
                break;
            }
            hasher.update(&buffer[..read]);
        }
        let after = query_file_identity(file.as_raw_handle().cast())?;
        if before != after {
            return Err(AuthorityPipeError::new(
                "authority_peer_controller_file_changed",
            ));
        }
        Ok((file, hasher.finalize().into(), before))
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

    fn current_process_session_id() -> Result<u32, AuthorityPipeError> {
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

    fn open_test_client(pipe_name: &str) -> Result<OwnedHandle, AuthorityPipeError> {
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

    fn unique_test_pipe_name() -> String {
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

    #[cfg(test)]
    mod tests {
        use super::*;

        fn policy() -> AuthorityPeerPolicy {
            let digest = [0x42; 32];
            let layout = AuthorityLayout::for_test_roots(
                Path::new(r"C:\Program Files"),
                Path::new(r"C:\ProgramData"),
            )
            .unwrap();
            AuthorityPeerPolicy::for_installed_layout(&layout, digest, 7).unwrap()
        }

        fn facts<'a>(path: &'a Path) -> AuthorityPeerFacts<'a> {
            AuthorityPeerFacts {
                controller_path: path,
                controller_sha256: [0x42; 32],
                pipe_session_id: 7,
                token_session_id: 7,
                elevated: true,
                high_integrity: true,
                administrators_member: true,
            }
        }

        #[test]
        fn policy_accepts_only_the_exact_high_administrator_controller() {
            let policy = policy();
            evaluate_peer_policy(&policy, &facts(policy.expected_controller_path())).unwrap();
        }

        #[test]
        fn policy_rejects_every_identity_shortcut() {
            let policy = policy();
            let expected_path = policy.expected_controller_path();
            let cases = [
                (
                    AuthorityPeerFacts {
                        elevated: false,
                        ..facts(expected_path)
                    },
                    "authority_peer_not_elevated",
                ),
                (
                    AuthorityPeerFacts {
                        high_integrity: false,
                        ..facts(expected_path)
                    },
                    "authority_peer_integrity_too_low",
                ),
                (
                    AuthorityPeerFacts {
                        administrators_member: false,
                        ..facts(expected_path)
                    },
                    "authority_peer_not_administrator",
                ),
                (
                    AuthorityPeerFacts {
                        pipe_session_id: 8,
                        ..facts(expected_path)
                    },
                    "authority_peer_session_mismatch",
                ),
                (
                    AuthorityPeerFacts {
                        token_session_id: 8,
                        ..facts(expected_path)
                    },
                    "authority_peer_session_mismatch",
                ),
                (
                    AuthorityPeerFacts {
                        controller_path: Path::new(
                            r"C:\Program Files\VRCForge\EvidenceAuthority\v1\controller-copy.exe",
                        ),
                        ..facts(expected_path)
                    },
                    "authority_peer_controller_path_mismatch",
                ),
                (
                    AuthorityPeerFacts {
                        controller_sha256: [0x43; 32],
                        ..facts(expected_path)
                    },
                    "authority_peer_controller_digest_mismatch",
                ),
            ];
            for (observed, expected_code) in cases {
                assert_eq!(
                    evaluate_peer_policy(&policy, &observed).unwrap_err().code(),
                    expected_code
                );
            }
        }

        #[test]
        fn policy_rejects_relative_or_traversing_controller_paths() {
            for path in [
                PathBuf::from("controller.exe"),
                PathBuf::from(r"C:\Program Files\VRCForge\..\controller.exe"),
            ] {
                assert_eq!(
                    AuthorityPeerPolicy::new(path, [0u8; 32], 1)
                        .unwrap_err()
                        .code(),
                    "authority_peer_controller_path_invalid"
                );
            }
        }

        #[test]
        fn policy_requires_an_exact_content_addressed_parent() {
            let digest = [0x42; 32];
            for (path, candidate_digest) in [
                (
                    PathBuf::from(
                        r"C:\Program Files\VRCForge\EvidenceAuthority\v1\vrcforge_primitive_evidence_controller.exe",
                    ),
                    digest,
                ),
                (
                    PathBuf::from(format!(
                        r"C:\Program Files\VRCForge\EvidenceAuthority\v1\{}\vrcforge_primitive_evidence_controller.exe",
                        "43".repeat(32)
                    )),
                    digest,
                ),
                (
                    PathBuf::from(format!(
                        r"C:\Program Files\VRCForge\EvidenceAuthority\v1\{}\vrcforge_primitive_evidence_controller.exe",
                        "00".repeat(32)
                    )),
                    [0; 32],
                ),
            ] {
                assert_eq!(
                    AuthorityPeerPolicy::new(path, candidate_digest, 1)
                        .unwrap_err()
                        .code(),
                    "authority_peer_controller_path_not_content_addressed"
                );
            }
        }

        #[test]
        fn production_sddl_parses_without_elevation() {
            let descriptor = SecurityDescriptor::from_sddl(AUTHORITY_PIPE_SDDL).unwrap();
            assert!(!descriptor.0.is_null());
        }

        #[test]
        fn first_instance_flag_prevents_a_second_real_server() {
            let name = unique_test_pipe_name();
            let _first = create_pipe_with_sddl(&name, TEST_PIPE_SDDL).unwrap();
            let error = create_pipe_with_sddl(&name, TEST_PIPE_SDDL).unwrap_err();
            assert_eq!(error.code(), "authority_pipe_create_failed");
        }

        #[test]
        fn current_process_cannot_obtain_identity_through_production_policy() {
            let name = unique_test_pipe_name();
            let pipe = create_pipe_with_sddl(&name, TEST_PIPE_SDDL).unwrap();
            let _client = open_test_client(&name).unwrap();
            let current_path = std::env::current_exe().unwrap();
            let mut digest: [u8; 32] = Sha256::digest(std::fs::read(&current_path).unwrap()).into();
            digest[0] ^= 0xff;
            let expected_path = current_path
                .parent()
                .unwrap()
                .join(hex_lower(&digest))
                .join("vrcforge_primitive_evidence_controller.exe");
            let policy = AuthorityPeerPolicy::new(
                expected_path,
                digest,
                current_process_session_id().unwrap(),
            )
            .unwrap();
            assert!(pipe.accept_peer(&policy).is_err());
        }

        #[test]
        fn self_test_is_non_mutating_and_does_not_need_elevation() {
            run_non_mutating_self_test().unwrap();
        }

        #[test]
        fn exited_process_with_still_active_exit_code_is_not_live() {
            let mut child = std::process::Command::new("cmd.exe")
                .args(["/C", "exit", "259"])
                .spawn()
                .unwrap();
            let status = child.wait().unwrap();
            assert_eq!(status.code(), Some(259));
            assert!(!process_is_active(child.as_raw_handle().cast()).unwrap());
        }
    }
}

#[cfg(windows)]
#[allow(unused_imports)]
pub use windows::{
    run_non_mutating_self_test, AuthorityPeerIdentity, AuthorityPipe, StableFileIdentity,
};

#[cfg(not(windows))]
pub fn run_non_mutating_self_test() -> Result<(), AuthorityPipeError> {
    Err(AuthorityPipeError::new(
        "authority_pipe_platform_unsupported",
    ))
}

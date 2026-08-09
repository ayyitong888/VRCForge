//! Parent-owned, fixed three-pipe transport for protected child entry points.
//!
//! This module deliberately stops before process creation. It constructs the
//! exact inherited endpoint set and keeps every parent endpoint affine so a
//! later launcher cannot retain a stray bootstrap writer or parent-side client
//! copy after a successful `CreateProcessW` call.
//! Parent construction binds asynchronous byte-mode, blocking-wait handles;
//! this is not a substitute for the child's independent kernel readback.

#![cfg(windows)]
#![cfg_attr(not(test), allow(dead_code))]

use super::{ProcessKey, SupervisorPolicy};
use crate::{
    primitive_evidence_authority_install::authority_service_sid,
    primitive_evidence_authority_runtime::AuthorityRuntimeIdentity,
    primitive_evidence_child_protocol::{
        ChildBootstrapRole, ChildStandardHandlePurpose, RoleRawHandleListDigest,
        CHILD_BOOTSTRAP_FRAME_LEN, CHILD_STANDARD_HANDLE_SLOT_COUNT,
    },
    primitive_evidence_process_token_windows::require_thread_without_impersonation_token,
};
use sha2::{Digest as Sha2Digest, Sha256};
use std::{
    fmt,
    mem::{size_of, zeroed},
    os::windows::{
        ffi::OsStrExt,
        io::{AsHandle, AsRawHandle, BorrowedHandle, FromRawHandle, OwnedHandle, RawHandle},
    },
    ptr::{self, null_mut},
    sync::atomic::{compiler_fence, Ordering},
    time::{Duration, Instant},
};
use windows_sys::Win32::{
    Foundation::{
        CompareObjectHandles, GetHandleInformation, GetLastError, LocalFree, ERROR_ACCESS_DENIED,
        ERROR_BROKEN_PIPE, ERROR_IO_PENDING, ERROR_OPERATION_ABORTED, ERROR_PIPE_CONNECTED,
        FILETIME, GENERIC_READ, GENERIC_WRITE, HANDLE, HANDLE_FLAG_INHERIT, INVALID_HANDLE_VALUE,
        WAIT_OBJECT_0, WAIT_TIMEOUT,
    },
    Security::{
        Authorization::{ConvertStringSecurityDescriptorToSecurityDescriptorW, SDDL_REVISION_1},
        PSECURITY_DESCRIPTOR, SECURITY_ATTRIBUTES,
    },
    Storage::FileSystem::{
        CreateFileW, ReadFile, WriteFile, FILE_ALL_ACCESS, FILE_FLAG_FIRST_PIPE_INSTANCE,
        FILE_FLAG_OVERLAPPED, FILE_GENERIC_READ, FILE_GENERIC_WRITE, FILE_READ_ATTRIBUTES,
        OPEN_EXISTING, PIPE_ACCESS_DUPLEX, PIPE_ACCESS_INBOUND, PIPE_ACCESS_OUTBOUND,
    },
    System::{
        Pipes::{
            ConnectNamedPipe, CreateNamedPipeW, GetNamedPipeClientProcessId, PIPE_READMODE_BYTE,
            PIPE_REJECT_REMOTE_CLIENTS, PIPE_TYPE_BYTE, PIPE_WAIT,
        },
        Threading::{
            CreateEventW, GetCurrentProcessId, GetProcessId, GetProcessIdOfThread, GetProcessTimes,
            GetThreadId, GetThreadTimes, WaitForSingleObject,
        },
        IO::{CancelIoEx, GetOverlappedResult, OVERLAPPED},
    },
};

#[cfg(test)]
use windows_sys::Win32::{
    Foundation::{
        CloseHandle, DuplicateHandle, DUPLICATE_SAME_ACCESS, ERROR_INSUFFICIENT_BUFFER,
        ERROR_SUCCESS,
    },
    Security::{
        Authorization::{ConvertSidToStringSidW, GetSecurityInfo, SE_KERNEL_OBJECT},
        GetAce, GetSecurityDescriptorControl, GetSecurityDescriptorDacl,
        GetSecurityDescriptorOwner, GetTokenInformation, IsValidSid, TokenUser, ACCESS_ALLOWED_ACE,
        ACL, DACL_SECURITY_INFORMATION, OWNER_SECURITY_INFORMATION, PSID, SE_DACL_PROTECTED,
        TOKEN_QUERY, TOKEN_USER,
    },
    Storage::FileSystem::{FILE_READ_DATA, FILE_TYPE_PIPE, FILE_WRITE_DATA},
    System::{
        Pipes::{
            GetNamedPipeHandleStateW, GetNamedPipeInfo, GetNamedPipeServerProcessId, PIPE_NOWAIT,
            PIPE_READMODE_MESSAGE, PIPE_SERVER_END,
        },
        SystemServices::ACCESS_ALLOWED_ACE_TYPE,
        Threading::{
            CreateProcessW, DeleteProcThreadAttributeList, GetCurrentProcess,
            GetProcessHandleCount, InitializeProcThreadAttributeList, OpenProcessToken,
            TerminateProcess, UpdateProcThreadAttribute, CREATE_NO_WINDOW, CREATE_SUSPENDED,
            EXTENDED_STARTUPINFO_PRESENT, LPPROC_THREAD_ATTRIBUTE_LIST, PROCESS_INFORMATION,
            PROC_THREAD_ATTRIBUTE_HANDLE_LIST, STARTUPINFOEXW,
        },
    },
};

type TransportDigest = [u8; 32];

const LOCAL_SYSTEM_SID: &str = "S-1-5-18";
const PIPE_NAME_PREFIX: &str = r"\\.\pipe\VRCForge.PrimitiveEvidence.Child.";
const PIPE_BUFFER_BYTES: u32 = 4 * 1024;
const PIPE_DEFAULT_TIMEOUT_MILLIS: u32 = 5_000;
const PIPE_CANCEL_SETTLE_MILLIS: u32 = 100;
const PIPE_SECURITY_BINDING_DOMAIN: &[u8] = b"vrcforge-child-parent-pipe-security-v1\0";
const PIPE_SET_BINDING_DOMAIN: &[u8] = b"vrcforge-child-parent-pipe-set-v1\0";
const PARENT_CLIENT_COPIES_CLOSED_DOMAIN: &[u8] =
    b"vrcforge-child-parent-client-copies-closed-v1\0";
const CREATED_CHILD_PRIMARY_THREAD_DOMAIN: &[u8] =
    b"vrcforge-parent-created-child-primary-thread-v1\0";
const CREATED_CHILD_LAUNCH_ATTRIBUTE_DOMAIN: &[u8] =
    b"vrcforge-parent-created-child-launch-attributes-v1\0";
const CREATED_SUSPENDED_CHILD_BINDING_DOMAIN: &[u8] =
    b"vrcforge-parent-created-suspended-child-v1\0";
const CREATED_CHILD_PRIMARY_THREAD_SOURCE_DOMAIN: &[u8] =
    b"vrcforge-parent-created-child-primary-thread-source-v1\0";
const CREATED_SUSPENDED_CHILD_PRODUCTION_BLOCKER: &str =
    "parent_created_suspended_child_evidence_not_connected";
const PIPE_NAME_RANDOM_BYTES: usize = 24;

const PIPE_CREATE_MODE: u32 =
    PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS;
const SERVER_COMMON_OPEN_MODE: u32 = FILE_FLAG_FIRST_PIPE_INSTANCE | FILE_FLAG_OVERLAPPED;
const CLIENT_COMMON_FLAGS: u32 = FILE_FLAG_OVERLAPPED;

#[derive(Clone, PartialEq, Eq)]
pub(super) struct ParentPipeSecuritySpec {
    authority_generation_digest: TransportDigest,
    authority_identity_digest: TransportDigest,
    owner_sid: String,
    service_sid: String,
    binding_digest: TransportDigest,
    test_owner_override: bool,
}

impl fmt::Debug for ParentPipeSecuritySpec {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ParentPipeSecuritySpec")
            .field("descriptor", &"<bound-and-redacted>")
            .finish()
    }
}

impl ParentPipeSecuritySpec {
    pub(super) fn from_runtime_identity(
        identity: &AuthorityRuntimeIdentity,
    ) -> Result<Self, ParentPipeError> {
        Self::from_parts(
            *identity.authority_generation_digest(),
            identity.binding_digest(),
            LOCAL_SYSTEM_SID.to_owned(),
            false,
        )
    }

    pub(super) fn from_supervisor_policy(
        policy: &SupervisorPolicy,
    ) -> Result<Self, ParentPipeError> {
        Self::from_parts(
            policy.authority_generation_digest,
            policy.authority_identity_digest,
            LOCAL_SYSTEM_SID.to_owned(),
            false,
        )
    }

    fn from_parts(
        authority_generation_digest: TransportDigest,
        authority_identity_digest: TransportDigest,
        owner_sid: String,
        test_owner_override: bool,
    ) -> Result<Self, ParentPipeError> {
        let service_sid = authority_service_sid().to_owned();
        let mut value = Self {
            authority_generation_digest,
            authority_identity_digest,
            owner_sid,
            service_sid,
            binding_digest: [0; 32],
            test_owner_override,
        };
        value.binding_digest = value.derive_binding_digest();
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), ParentPipeError> {
        if self
            .authority_generation_digest
            .iter()
            .all(|byte| *byte == 0)
            || self.authority_identity_digest.iter().all(|byte| *byte == 0)
            || self.owner_sid.is_empty()
            || self.owner_sid.contains('\0')
            || self.service_sid != authority_service_sid()
            || (!self.test_owner_override && self.owner_sid != LOCAL_SYSTEM_SID)
            || self.binding_digest.iter().all(|byte| *byte == 0)
            || self.binding_digest != self.derive_binding_digest()
        {
            return Err(ParentPipeError::new("parent_pipe_security_spec_invalid"));
        }
        Ok(())
    }

    fn derive_binding_digest(&self) -> TransportDigest {
        let mut digest = Sha256::new();
        digest.update(PIPE_SECURITY_BINDING_DOMAIN);
        digest.update(self.authority_generation_digest);
        digest.update(self.authority_identity_digest);
        hash_string(&mut digest, &self.owner_sid);
        hash_string(&mut digest, &self.service_sid);
        digest.update([u8::from(self.test_owner_override)]);
        digest.update(PIPE_CREATE_MODE.to_be_bytes());
        digest.update(CLIENT_COMMON_FLAGS.to_be_bytes());
        digest.update(PIPE_BUFFER_BYTES.to_be_bytes());
        digest.update(PIPE_DEFAULT_TIMEOUT_MILLIS.to_be_bytes());
        for slot in PipeSlot::ALL {
            digest.update([slot.wire_value()]);
            digest.update(slot.client_desired_access().to_be_bytes());
            digest.update(self.descriptor_access(slot).to_be_bytes());
            digest.update(slot.server_open_mode().to_be_bytes());
        }
        digest.finalize().into()
    }

    fn descriptor_for(&self, slot: PipeSlot) -> Result<OwnedSecurityDescriptor, ParentPipeError> {
        self.validate()?;
        let access = self.descriptor_access(slot);
        let sddl = if self.test_owner_override {
            format!(
                "O:{owner}D:P(A;;0x{access:08x};;;{owner})",
                owner = self.owner_sid
            )
        } else {
            format!(
                "O:SYD:P(A;;0x{access:08x};;;SY)(A;;0x{access:08x};;;{service})",
                service = self.service_sid
            )
        };
        OwnedSecurityDescriptor::from_sddl(&sddl)
    }

    fn descriptor_access(&self, slot: PipeSlot) -> u32 {
        if self.test_owner_override {
            // Test-only: a single ordinary-user principal must be able to
            // create both endpoints in-process. The inherited handle still
            // receives only its exact requested access.
            FILE_ALL_ACCESS
        } else {
            slot.client_acl_access()
        }
    }

    #[cfg(test)]
    fn for_test_current_process() -> Result<Self, ParentPipeError> {
        Self::from_parts(
            [0x41; 32],
            [0x42; 32],
            current_process_user_sid_string()?,
            true,
        )
    }
}

fn hash_string(digest: &mut Sha256, value: &str) {
    digest.update((value.len() as u32).to_be_bytes());
    digest.update(value.as_bytes());
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(u8)]
enum PipeSlot {
    Bootstrap = 1,
    Control = 2,
    Result = 3,
}

impl PipeSlot {
    const ALL: [Self; CHILD_STANDARD_HANDLE_SLOT_COUNT] =
        [Self::Bootstrap, Self::Control, Self::Result];

    const fn wire_value(self) -> u8 {
        self as u8
    }

    const fn purpose(self) -> ChildStandardHandlePurpose {
        match self {
            Self::Bootstrap => ChildStandardHandlePurpose::BootstrapRead,
            Self::Control => ChildStandardHandlePurpose::PrivateControlDuplex,
            Self::Result => ChildStandardHandlePurpose::StructuredResultWrite,
        }
    }

    const fn client_desired_access(self) -> u32 {
        let contract = self.purpose().access_contract();
        let mut access = 0;
        if contract.readable() {
            access |= GENERIC_READ;
        }
        if contract.writable() {
            access |= GENERIC_WRITE;
        }
        access
    }

    const fn client_acl_access(self) -> u32 {
        let contract = self.purpose().access_contract();
        let mut access = 0;
        if contract.readable() {
            access |= FILE_GENERIC_READ;
        }
        if contract.writable() {
            access |= FILE_GENERIC_WRITE;
        }
        if contract.writable() && !contract.readable() {
            // Windows grants this metadata-query bit on a write-only named-pipe
            // client. It is part of the exact child readback contract.
            access |= FILE_READ_ATTRIBUTES;
        }
        access
    }

    const fn server_open_mode(self) -> u32 {
        let direction = match self {
            Self::Bootstrap => PIPE_ACCESS_OUTBOUND,
            Self::Control => PIPE_ACCESS_DUPLEX,
            Self::Result => PIPE_ACCESS_INBOUND,
        };
        direction | SERVER_COMMON_OPEN_MODE
    }
}

#[derive(Clone, PartialEq, Eq)]
pub(super) struct ParentPipeError(&'static str);

impl ParentPipeError {
    const fn new(code: &'static str) -> Self {
        Self(code)
    }

    pub(super) fn requires_session_containment(&self) -> bool {
        self.0 == "parent_pipe_io_quarantined"
    }

    pub(super) const fn code(&self) -> &'static str {
        self.0
    }

    #[cfg(test)]
    pub(super) const fn quarantined_for_test() -> Self {
        Self::new("parent_pipe_io_quarantined")
    }
}

impl fmt::Debug for ParentPipeError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_tuple("ParentPipeError")
            .field(&self.0)
            .finish()
    }
}

impl fmt::Display for ParentPipeError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.0)
    }
}

impl std::error::Error for ParentPipeError {}

#[derive(Clone, Copy, PartialEq, Eq)]
pub(super) struct ParentPipeSetBindingDigest(TransportDigest);

impl ParentPipeSetBindingDigest {
    fn derive(
        security: &ParentPipeSecuritySpec,
        nonce: &[u8; PIPE_NAME_RANDOM_BYTES],
        pipes: [&ConnectedPipe; CHILD_STANDARD_HANDLE_SLOT_COUNT],
    ) -> Result<Self, ParentPipeError> {
        let mut digest = Sha256::new();
        digest.update(PIPE_SET_BINDING_DOMAIN);
        digest.update(security.binding_digest);
        digest.update((nonce.len() as u16).to_be_bytes());
        digest.update(nonce);
        digest.update((pipes.len() as u16).to_be_bytes());
        for (slot, pipe) in PipeSlot::ALL.into_iter().zip(pipes) {
            if pipe.slot != slot {
                return Err(ParentPipeError::new("parent_pipe_set_order_invalid"));
            }
            digest.update([slot.wire_value()]);
            digest.update((pipe.server_raw() as usize).to_ne_bytes());
            digest.update((pipe.client_raw() as usize).to_ne_bytes());
        }
        let value = Self(digest.finalize().into());
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), ParentPipeError> {
        if self.0.iter().all(|byte| *byte == 0) {
            return Err(ParentPipeError::new("parent_pipe_set_binding_invalid"));
        }
        Ok(())
    }

    pub(super) fn as_bytes(&self) -> &TransportDigest {
        &self.0
    }
}

impl fmt::Debug for ParentPipeSetBindingDigest {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("ParentPipeSetBindingDigest(<redacted>)")
    }
}

macro_rules! define_created_child_binding_digest {
    ($name:ident, $domain:ident, $error:literal) => {
        #[derive(Clone, Copy, PartialEq, Eq)]
        pub(super) struct $name(TransportDigest);

        impl $name {
            fn derive(source: &TransportDigest) -> Result<Self, ParentPipeError> {
                if source.iter().all(|byte| *byte == 0) {
                    return Err(ParentPipeError::new($error));
                }
                let mut digest = Sha256::new();
                digest.update($domain);
                digest.update((source.len() as u16).to_be_bytes());
                digest.update(source);
                let value = Self(digest.finalize().into());
                if value.0.iter().all(|byte| *byte == 0) {
                    return Err(ParentPipeError::new($error));
                }
                Ok(value)
            }

            pub(super) fn as_bytes(&self) -> &TransportDigest {
                &self.0
            }
        }

        impl fmt::Debug for $name {
            fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter.write_str(concat!(stringify!($name), "(<redacted>)"))
            }
        }
    };
}

define_created_child_binding_digest!(
    CreatedChildPrimaryThreadBindingDigest,
    CREATED_CHILD_PRIMARY_THREAD_DOMAIN,
    "parent_created_child_primary_thread_binding_invalid"
);
define_created_child_binding_digest!(
    CreatedChildLaunchAttributeBindingDigest,
    CREATED_CHILD_LAUNCH_ATTRIBUTE_DOMAIN,
    "parent_created_child_launch_attribute_binding_invalid"
);

pub(super) struct CreatedChildProcessThreadObservation {
    role: ChildBootstrapRole,
    process_key: ProcessKey,
    primary_thread_id: u32,
    primary_thread_creation_time: u64,
    primary_thread_binding: CreatedChildPrimaryThreadBindingDigest,
}

impl CreatedChildProcessThreadObservation {
    pub(super) const fn role(&self) -> ChildBootstrapRole {
        self.role
    }

    pub(super) const fn process_key(&self) -> ProcessKey {
        self.process_key
    }

    pub(super) const fn primary_thread_id(&self) -> u32 {
        self.primary_thread_id
    }

    pub(super) const fn primary_thread_creation_time(&self) -> u64 {
        self.primary_thread_creation_time
    }

    pub(super) const fn primary_thread_binding(&self) -> CreatedChildPrimaryThreadBindingDigest {
        self.primary_thread_binding
    }
}

pub(super) fn observe_live_created_child_process_and_thread(
    role: ChildBootstrapRole,
    process: BorrowedHandle<'_>,
    primary_thread: BorrowedHandle<'_>,
) -> Result<CreatedChildProcessThreadObservation, ParentPipeError> {
    let process = process.as_raw_handle().cast();
    let primary_thread = primary_thread.as_raw_handle().cast();
    if !handle_is_valid(process)
        || !handle_is_valid(primary_thread)
        || unsafe { WaitForSingleObject(process, 0) } != WAIT_TIMEOUT
        || unsafe { WaitForSingleObject(primary_thread, 0) } != WAIT_TIMEOUT
    {
        return Err(ParentPipeError::new(
            "parent_created_child_live_handle_invalid",
        ));
    }
    require_thread_without_impersonation_token(primary_thread)
        .map_err(|error| ParentPipeError::new(error.code()))?;
    let process_id = unsafe { GetProcessId(process) };
    let primary_thread_id = unsafe { GetThreadId(primary_thread) };
    if process_id == 0
        || primary_thread_id == 0
        || unsafe { GetProcessIdOfThread(primary_thread) } != process_id
    {
        return Err(ParentPipeError::new(
            "parent_created_child_live_handle_invalid",
        ));
    }
    let mut process_creation: FILETIME = unsafe { zeroed() };
    let mut process_exit: FILETIME = unsafe { zeroed() };
    let mut process_kernel: FILETIME = unsafe { zeroed() };
    let mut process_user: FILETIME = unsafe { zeroed() };
    let mut thread_creation: FILETIME = unsafe { zeroed() };
    let mut thread_exit: FILETIME = unsafe { zeroed() };
    let mut thread_kernel: FILETIME = unsafe { zeroed() };
    let mut thread_user: FILETIME = unsafe { zeroed() };
    if unsafe {
        GetProcessTimes(
            process,
            &mut process_creation,
            &mut process_exit,
            &mut process_kernel,
            &mut process_user,
        )
    } == 0
        || unsafe {
            GetThreadTimes(
                primary_thread,
                &mut thread_creation,
                &mut thread_exit,
                &mut thread_kernel,
                &mut thread_user,
            )
        } == 0
    {
        return Err(ParentPipeError::new(
            "parent_created_child_live_epoch_unavailable",
        ));
    }
    let process_key = ProcessKey {
        pid: process_id,
        creation_time: file_time_value(process_creation),
    };
    let primary_thread_creation_time = file_time_value(thread_creation);
    let primary_thread_binding = created_child_primary_thread_binding(
        role,
        process_key,
        primary_thread_id,
        primary_thread_creation_time,
    )?;
    Ok(CreatedChildProcessThreadObservation {
        role,
        process_key,
        primary_thread_id,
        primary_thread_creation_time,
        primary_thread_binding,
    })
}

/// Affine evidence returned only by a successful future suspended-process
/// creation adapter. The current slice deliberately cannot construct it in
/// production, so a close token cannot be minted without that missing event.
pub(super) struct CreatedSuspendedChildClosureBinding {
    role: ChildBootstrapRole,
    process_key: ProcessKey,
    primary_thread: CreatedChildPrimaryThreadBindingDigest,
    launch_attributes: CreatedChildLaunchAttributeBindingDigest,
    binding_digest: TransportDigest,
}

impl fmt::Debug for CreatedSuspendedChildClosureBinding {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("CreatedSuspendedChildClosureBinding")
            .field("role", &self.role)
            .field("process", &"<held-and-redacted>")
            .field("binding", &"<redacted>")
            .finish()
    }
}

impl CreatedSuspendedChildClosureBinding {
    pub(super) fn from_production_create_result() -> Result<Self, ParentPipeError> {
        Err(ParentPipeError::new(
            CREATED_SUSPENDED_CHILD_PRODUCTION_BLOCKER,
        ))
    }

    pub(super) fn from_held_suspended_create_result(
        role: ChildBootstrapRole,
        process: HANDLE,
        primary_thread: HANDLE,
        expected_process_id: u32,
        expected_primary_thread_id: u32,
        launch_attribute_source: TransportDigest,
    ) -> Result<Self, ParentPipeError> {
        if !handle_is_valid(process)
            || !handle_is_valid(primary_thread)
            || expected_process_id == 0
            || expected_primary_thread_id == 0
            || launch_attribute_source.iter().all(|byte| *byte == 0)
        {
            return Err(ParentPipeError::new(
                "parent_created_suspended_child_handle_invalid",
            ));
        }
        let process = unsafe { BorrowedHandle::borrow_raw(process as RawHandle) };
        let primary_thread = unsafe { BorrowedHandle::borrow_raw(primary_thread as RawHandle) };
        let observation =
            observe_live_created_child_process_and_thread(role, process, primary_thread).map_err(
                |error| {
                    if error.code() == "protected_child_thread_impersonation_token_forbidden" {
                        error
                    } else {
                        ParentPipeError::new("parent_created_suspended_child_handle_invalid")
                    }
                },
            )?;
        if observation.process_key.pid != expected_process_id
            || observation.primary_thread_id != expected_primary_thread_id
        {
            return Err(ParentPipeError::new(
                "parent_created_suspended_child_handle_invalid",
            ));
        }
        Self::new(
            role,
            observation.process_key,
            observation.primary_thread_binding,
            CreatedChildLaunchAttributeBindingDigest::derive(&launch_attribute_source)?,
        )
    }

    fn new(
        role: ChildBootstrapRole,
        process_key: ProcessKey,
        primary_thread: CreatedChildPrimaryThreadBindingDigest,
        launch_attributes: CreatedChildLaunchAttributeBindingDigest,
    ) -> Result<Self, ParentPipeError> {
        let mut digest = Sha256::new();
        digest.update(CREATED_SUSPENDED_CHILD_BINDING_DOMAIN);
        digest.update([role.wire_value()]);
        digest.update(process_key.pid.to_be_bytes());
        digest.update(process_key.creation_time.to_be_bytes());
        digest.update(primary_thread.as_bytes());
        digest.update(launch_attributes.as_bytes());
        let value = Self {
            role,
            process_key,
            primary_thread,
            launch_attributes,
            binding_digest: digest.finalize().into(),
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), ParentPipeError> {
        if self.process_key.pid == 0
            || self.process_key.creation_time == 0
            || self.primary_thread.as_bytes() == self.launch_attributes.as_bytes()
            || self.binding_digest.iter().all(|byte| *byte == 0)
            || self.binding_digest != self.derive_binding_digest()
        {
            return Err(ParentPipeError::new(
                "parent_created_suspended_child_binding_invalid",
            ));
        }
        Ok(())
    }

    fn derive_binding_digest(&self) -> TransportDigest {
        let mut digest = Sha256::new();
        digest.update(CREATED_SUSPENDED_CHILD_BINDING_DOMAIN);
        digest.update([self.role.wire_value()]);
        digest.update(self.process_key.pid.to_be_bytes());
        digest.update(self.process_key.creation_time.to_be_bytes());
        digest.update(self.primary_thread.as_bytes());
        digest.update(self.launch_attributes.as_bytes());
        digest.finalize().into()
    }

    pub(super) const fn role(&self) -> ChildBootstrapRole {
        self.role
    }

    pub(super) const fn process_key(&self) -> ProcessKey {
        self.process_key
    }

    pub(super) const fn primary_thread_binding(&self) -> CreatedChildPrimaryThreadBindingDigest {
        self.primary_thread
    }

    pub(super) const fn launch_attribute_binding(
        &self,
    ) -> CreatedChildLaunchAttributeBindingDigest {
        self.launch_attributes
    }

    pub(super) fn binding_digest(&self) -> &TransportDigest {
        &self.binding_digest
    }

    #[cfg(test)]
    pub(super) fn for_test(
        role: ChildBootstrapRole,
        process_key: ProcessKey,
        primary_thread_source: TransportDigest,
        launch_attribute_source: TransportDigest,
    ) -> Result<Self, ParentPipeError> {
        Self::new(
            role,
            process_key,
            CreatedChildPrimaryThreadBindingDigest::derive(&primary_thread_source)?,
            CreatedChildLaunchAttributeBindingDigest::derive(&launch_attribute_source)?,
        )
    }
}

pub(super) fn created_child_primary_thread_binding(
    role: ChildBootstrapRole,
    process_key: ProcessKey,
    primary_thread_id: u32,
    primary_thread_creation_time: u64,
) -> Result<CreatedChildPrimaryThreadBindingDigest, ParentPipeError> {
    if process_key.pid == 0
        || process_key.creation_time == 0
        || primary_thread_id == 0
        || primary_thread_creation_time == 0
    {
        return Err(ParentPipeError::new(
            "parent_created_child_primary_thread_binding_invalid",
        ));
    }
    let mut source = Sha256::new();
    source.update(CREATED_CHILD_PRIMARY_THREAD_SOURCE_DOMAIN);
    source.update([role.wire_value()]);
    source.update(process_key.pid.to_be_bytes());
    source.update(process_key.creation_time.to_be_bytes());
    source.update(primary_thread_id.to_be_bytes());
    source.update(primary_thread_creation_time.to_be_bytes());
    CreatedChildPrimaryThreadBindingDigest::derive(&source.finalize().into())
}

struct OwnedSecurityDescriptor(PSECURITY_DESCRIPTOR);

impl OwnedSecurityDescriptor {
    fn from_sddl(value: &str) -> Result<Self, ParentPipeError> {
        if value.is_empty() || value.contains('\0') {
            return Err(ParentPipeError::new(
                "parent_pipe_security_descriptor_invalid",
            ));
        }
        let encoded = value
            .encode_utf16()
            .chain(std::iter::once(0))
            .collect::<Vec<_>>();
        let mut descriptor = null_mut();
        if unsafe {
            ConvertStringSecurityDescriptorToSecurityDescriptorW(
                encoded.as_ptr(),
                SDDL_REVISION_1,
                &mut descriptor,
                null_mut(),
            )
        } == 0
            || descriptor.is_null()
        {
            if !descriptor.is_null() {
                unsafe {
                    LocalFree(descriptor);
                }
            }
            return Err(ParentPipeError::new(
                "parent_pipe_security_descriptor_invalid",
            ));
        }
        Ok(Self(descriptor))
    }

    fn raw(&self) -> PSECURITY_DESCRIPTOR {
        self.0
    }
}

impl Drop for OwnedSecurityDescriptor {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe {
                LocalFree(self.0);
            }
            self.0 = null_mut();
        }
    }
}

pub(super) struct ParentHandleExclusions {
    job: HANDLE,
    completion_port: HANDLE,
}

impl fmt::Debug for ParentHandleExclusions {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ParentHandleExclusions")
            .field("handles", &"<redacted>")
            .finish()
    }
}

impl ParentHandleExclusions {
    pub(super) fn new(job: HANDLE, completion_port: HANDLE) -> Result<Self, ParentPipeError> {
        let value = Self {
            job,
            completion_port,
        };
        value.validate()?;
        Ok(value)
    }

    pub(super) fn from_borrowed(
        job: BorrowedHandle<'_>,
        completion_port: BorrowedHandle<'_>,
    ) -> Result<Self, ParentPipeError> {
        Self::new(
            job.as_raw_handle().cast(),
            completion_port.as_raw_handle().cast(),
        )
    }

    fn validate(&self) -> Result<(), ParentPipeError> {
        if !handle_is_valid(self.job)
            || !handle_is_valid(self.completion_port)
            || handles_alias(self.job, self.completion_port)
        {
            return Err(ParentPipeError::new(
                "parent_pipe_excluded_handle_set_invalid",
            ));
        }
        Ok(())
    }

    fn all(&self) -> [HANDLE; 2] {
        [self.job, self.completion_port]
    }
}

/// Owns all six endpoints until the launcher explicitly splits off the three
/// inheritable client copies. No pipe name survives successful construction.
pub(super) struct ParentPipeSet {
    bootstrap: ConnectedPipe,
    control: ConnectedPipe,
    result: ConnectedPipe,
    binding_digest: ParentPipeSetBindingDigest,
}

impl fmt::Debug for ParentPipeSet {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ParentPipeSet")
            .field("endpoints", &"<exact-three-redacted>")
            .finish()
    }
}

impl ParentPipeSet {
    pub(super) fn create(
        security: &ParentPipeSecuritySpec,
        exclusions: &ParentHandleExclusions,
    ) -> Result<Self, ParentPipeError> {
        Self::create_inner(security, exclusions, None)
    }

    fn create_inner(
        security: &ParentPipeSecuritySpec,
        exclusions: &ParentHandleExclusions,
        fail_before_slot: Option<usize>,
    ) -> Result<Self, ParentPipeError> {
        security.validate()?;
        exclusions.validate()?;
        let mut nonce = [0u8; PIPE_NAME_RANDOM_BYTES];
        getrandom::fill(&mut nonce)
            .map_err(|_| ParentPipeError::new("parent_pipe_secure_random_unavailable"))?;
        let prefix = format!("{PIPE_NAME_PREFIX}{}", encode_hex(&nonce));

        if fail_before_slot == Some(0) {
            return Err(ParentPipeError::new("parent_pipe_test_create_fault"));
        }
        let bootstrap = ConnectedPipe::create(
            security,
            PipeSlot::Bootstrap,
            &format!("{prefix}.bootstrap"),
        )?;
        if fail_before_slot == Some(1) {
            return Err(ParentPipeError::new("parent_pipe_test_create_fault"));
        }
        let control =
            ConnectedPipe::create(security, PipeSlot::Control, &format!("{prefix}.control"))?;
        if fail_before_slot == Some(2) {
            return Err(ParentPipeError::new("parent_pipe_test_create_fault"));
        }
        let result =
            ConnectedPipe::create(security, PipeSlot::Result, &format!("{prefix}.result"))?;
        if fail_before_slot == Some(3) {
            return Err(ParentPipeError::new("parent_pipe_test_create_fault"));
        }

        let binding_digest =
            ParentPipeSetBindingDigest::derive(security, &nonce, [&bootstrap, &control, &result])?;
        let value = Self {
            bootstrap,
            control,
            result,
            binding_digest,
        };
        value.validate_aliases(exclusions)?;
        Ok(value)
    }

    fn validate_aliases(&self, exclusions: &ParentHandleExclusions) -> Result<(), ParentPipeError> {
        let clients = [
            self.bootstrap.client_raw(),
            self.control.client_raw(),
            self.result.client_raw(),
        ];
        for (index, handle) in clients.iter().enumerate() {
            if !handle_is_valid(*handle)
                || clients[..index]
                    .iter()
                    .any(|prior| handles_alias(*prior, *handle))
                || exclusions
                    .all()
                    .iter()
                    .any(|excluded| handles_alias(*excluded, *handle))
            {
                return Err(ParentPipeError::new("parent_pipe_handle_alias"));
            }
        }
        Ok(())
    }

    pub(super) fn take_inherited_client_handles(
        self,
    ) -> (ParentPipeServers, InheritedClientHandleLease) {
        let Self {
            bootstrap,
            control,
            result,
            binding_digest,
        } = self;
        let (bootstrap_server, bootstrap_client) = bootstrap.into_parts();
        let (control_server, control_client) = control.into_parts();
        let (result_server, result_client) = result.into_parts();
        (
            ParentPipeServers {
                bootstrap: BootstrapPipeWriter(bootstrap_server),
                control: ParentControlPipe(control_server),
                result: ParentResultPipe(result_server),
                binding_digest,
            },
            InheritedClientHandleLease {
                clients: [bootstrap_client, control_client, result_client],
                pipe_set_binding_digest: binding_digest,
            },
        )
    }
}

struct ConnectedPipe {
    slot: PipeSlot,
    server: OwnedHandle,
    client: OwnedHandle,
}

impl ConnectedPipe {
    fn create(
        security: &ParentPipeSecuritySpec,
        slot: PipeSlot,
        pipe_name: &str,
    ) -> Result<Self, ParentPipeError> {
        let descriptor = security.descriptor_for(slot)?;
        let mut server_attributes = SECURITY_ATTRIBUTES {
            nLength: size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: descriptor.raw(),
            bInheritHandle: 0,
        };
        let pipe_name = wide_null(pipe_name);
        let raw_server = unsafe {
            CreateNamedPipeW(
                pipe_name.as_ptr(),
                slot.server_open_mode(),
                PIPE_CREATE_MODE,
                1,
                PIPE_BUFFER_BYTES,
                PIPE_BUFFER_BYTES,
                PIPE_DEFAULT_TIMEOUT_MILLIS,
                &mut server_attributes,
            )
        };
        if raw_server == INVALID_HANDLE_VALUE {
            return Err(ParentPipeError::new("parent_pipe_server_create_failed"));
        }
        let server = unsafe { OwnedHandle::from_raw_handle(raw_server as RawHandle) };

        let mut client_attributes = SECURITY_ATTRIBUTES {
            nLength: size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: ptr::null_mut(),
            bInheritHandle: 1,
        };
        let raw_client = unsafe {
            CreateFileW(
                pipe_name.as_ptr(),
                slot.client_desired_access(),
                0,
                &mut client_attributes,
                OPEN_EXISTING,
                CLIENT_COMMON_FLAGS,
                ptr::null_mut(),
            )
        };
        if raw_client == INVALID_HANDLE_VALUE {
            return Err(ParentPipeError::new(
                if unsafe { GetLastError() } == ERROR_ACCESS_DENIED {
                    "parent_pipe_client_open_access_denied"
                } else {
                    "parent_pipe_client_open_failed"
                },
            ));
        }
        let value = Self {
            slot,
            server,
            client: unsafe { OwnedHandle::from_raw_handle(raw_client as RawHandle) },
        };
        connect_server_after_client(value.server_raw(), Duration::from_secs(5))?;
        value.validate_handle_flags()?;
        Ok(value)
    }

    fn validate_handle_flags(&self) -> Result<(), ParentPipeError> {
        let mut server_flags = 0u32;
        let mut client_flags = 0u32;
        if unsafe { GetHandleInformation(self.server_raw(), &mut server_flags) } == 0
            || unsafe { GetHandleInformation(self.client_raw(), &mut client_flags) } == 0
            || server_flags & HANDLE_FLAG_INHERIT != 0
            || client_flags & HANDLE_FLAG_INHERIT == 0
        {
            return Err(ParentPipeError::new("parent_pipe_inherit_contract_invalid"));
        }
        Ok(())
    }

    fn server_raw(&self) -> HANDLE {
        self.server.as_raw_handle().cast()
    }

    fn client_raw(&self) -> HANDLE {
        self.client.as_raw_handle().cast()
    }

    fn into_parts(self) -> (OwnedHandle, OwnedHandle) {
        (self.server, self.client)
    }
}

impl fmt::Debug for ConnectedPipe {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ConnectedPipe")
            .field("slot", &self.slot)
            .field("handles", &"<redacted>")
            .finish()
    }
}

pub(super) struct InheritedClientHandleLease {
    clients: [OwnedHandle; CHILD_STANDARD_HANDLE_SLOT_COUNT],
    pipe_set_binding_digest: ParentPipeSetBindingDigest,
}

impl fmt::Debug for InheritedClientHandleLease {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("InheritedClientHandleLease")
            .field("order", &"<bootstrap-control-result>")
            .field("handles", &"<redacted>")
            .finish()
    }
}

impl InheritedClientHandleLease {
    /// Exact STARTUPINFOEX standard-handle order: input, output, error.
    pub(super) fn inherited_raw_handles(&self) -> [HANDLE; CHILD_STANDARD_HANDLE_SLOT_COUNT] {
        self.clients
            .each_ref()
            .map(|handle| handle.as_raw_handle().cast())
    }

    pub(super) fn inherited_borrowed_handles(
        &self,
    ) -> [BorrowedHandle<'_>; CHILD_STANDARD_HANDLE_SLOT_COUNT] {
        self.clients.each_ref().map(|handle| handle.as_handle())
    }

    /// Must be called immediately after successful process creation. Consuming
    /// the lease closes all parent copies before the child can be resumed.
    pub(super) fn close_parent_copies_after_create(
        self,
        created_child: CreatedSuspendedChildClosureBinding,
    ) -> Result<ParentClientCopiesClosed, ParentPipeError> {
        created_child.validate()?;
        let role = created_child.role();
        let raw_handles = self.inherited_raw_handles().map(|handle| handle as usize);
        let raw_handle_list_digest = RoleRawHandleListDigest::derive(role, &raw_handles)
            .map_err(|_| ParentPipeError::new("parent_pipe_closed_raw_handle_list_invalid"))?;
        let pipe_set_binding_digest = self.pipe_set_binding_digest;
        drop(self);
        ParentClientCopiesClosed::new(
            created_child,
            pipe_set_binding_digest,
            raw_handle_list_digest,
        )
    }

    /// A failed process creation follows the same affine close path.
    pub(super) fn close_after_create_failure(self) {
        drop(self);
    }
}

pub(super) struct ParentClientCopiesClosed {
    created_child: CreatedSuspendedChildClosureBinding,
    pipe_set_binding_digest: ParentPipeSetBindingDigest,
    raw_handle_list_digest: RoleRawHandleListDigest,
    closure_binding_digest: TransportDigest,
}

impl ParentClientCopiesClosed {
    fn new(
        created_child: CreatedSuspendedChildClosureBinding,
        pipe_set_binding_digest: ParentPipeSetBindingDigest,
        raw_handle_list_digest: RoleRawHandleListDigest,
    ) -> Result<Self, ParentPipeError> {
        created_child.validate()?;
        let role = created_child.role();
        if raw_handle_list_digest.role() != role {
            return Err(ParentPipeError::new(
                "parent_pipe_closed_raw_handle_role_invalid",
            ));
        }
        pipe_set_binding_digest.validate()?;
        let mut digest = Sha256::new();
        digest.update(PARENT_CLIENT_COPIES_CLOSED_DOMAIN);
        digest.update([role.wire_value()]);
        digest.update(created_child.binding_digest());
        digest.update(pipe_set_binding_digest.as_bytes());
        digest.update(raw_handle_list_digest.as_bytes());
        let closure_binding_digest: TransportDigest = digest.finalize().into();
        if closure_binding_digest.iter().all(|byte| *byte == 0) {
            return Err(ParentPipeError::new(
                "parent_pipe_client_closure_binding_invalid",
            ));
        }
        let value = Self {
            created_child,
            pipe_set_binding_digest,
            raw_handle_list_digest,
            closure_binding_digest,
        };
        value.validate()?;
        Ok(value)
    }

    pub(super) fn validate(&self) -> Result<(), ParentPipeError> {
        self.created_child.validate()?;
        self.pipe_set_binding_digest.validate()?;
        if self.raw_handle_list_digest.role() != self.role()
            || self.closure_binding_digest.iter().all(|byte| *byte == 0)
            || self.closure_binding_digest != self.derive_closure_binding_digest()
        {
            return Err(ParentPipeError::new(
                "parent_pipe_client_closure_binding_invalid",
            ));
        }
        Ok(())
    }

    fn derive_closure_binding_digest(&self) -> TransportDigest {
        let mut digest = Sha256::new();
        digest.update(PARENT_CLIENT_COPIES_CLOSED_DOMAIN);
        digest.update([self.role().wire_value()]);
        digest.update(self.created_child.binding_digest());
        digest.update(self.pipe_set_binding_digest.as_bytes());
        digest.update(self.raw_handle_list_digest.as_bytes());
        digest.finalize().into()
    }

    pub(super) const fn role(&self) -> ChildBootstrapRole {
        self.created_child.role()
    }

    pub(super) const fn process_key(&self) -> ProcessKey {
        self.created_child.process_key()
    }

    pub(super) const fn primary_thread_binding(&self) -> CreatedChildPrimaryThreadBindingDigest {
        self.created_child.primary_thread_binding()
    }

    pub(super) const fn launch_attribute_binding(
        &self,
    ) -> CreatedChildLaunchAttributeBindingDigest {
        self.created_child.launch_attribute_binding()
    }

    pub(super) fn created_child_binding_digest(&self) -> &TransportDigest {
        self.created_child.binding_digest()
    }

    pub(super) const fn pipe_set_binding_digest(&self) -> ParentPipeSetBindingDigest {
        self.pipe_set_binding_digest
    }

    pub(super) const fn raw_handle_list_digest(&self) -> RoleRawHandleListDigest {
        self.raw_handle_list_digest
    }

    pub(super) fn closure_binding_digest(&self) -> &TransportDigest {
        &self.closure_binding_digest
    }
}

impl fmt::Debug for ParentClientCopiesClosed {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("ParentClientCopiesClosed")
    }
}

pub(super) struct ParentPipeServers {
    pub(super) bootstrap: BootstrapPipeWriter,
    pub(super) control: ParentControlPipe,
    pub(super) result: ParentResultPipe,
    binding_digest: ParentPipeSetBindingDigest,
}

impl fmt::Debug for ParentPipeServers {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ParentPipeServers")
            .field("endpoints", &"<exact-three-redacted>")
            .finish()
    }
}

impl ParentPipeServers {
    pub(super) const fn pipe_set_binding_digest(&self) -> ParentPipeSetBindingDigest {
        self.binding_digest
    }

    /// This is only a creator-parent sanity check. Windows reports the process
    /// that created each client endpoint, not the process currently holding an
    /// inherited copy, so this result is never child identity evidence.
    pub(super) fn verify_client_creator_is_current_parent(&self) -> Result<(), ParentPipeError> {
        let expected = unsafe { GetCurrentProcessId() };
        if expected == 0 {
            return Err(ParentPipeError::new(
                "parent_pipe_client_creator_sanity_failed",
            ));
        }
        for server in [
            self.bootstrap.0.as_raw_handle().cast(),
            self.control.0.as_raw_handle().cast(),
            self.result.0.as_raw_handle().cast(),
        ] {
            let mut observed = 0u32;
            if unsafe { GetNamedPipeClientProcessId(server, &mut observed) } == 0
                || observed != expected
            {
                return Err(ParentPipeError::new(
                    "parent_pipe_client_creator_sanity_failed",
                ));
            }
        }
        Ok(())
    }

    pub(super) fn into_handshake_parts(
        self,
    ) -> (
        BootstrapPipeWriter,
        ParentControlPipe,
        ParentResultPipe,
        ParentPipeSetBindingDigest,
    ) {
        (
            self.bootstrap,
            self.control,
            self.result,
            self.binding_digest,
        )
    }
}

pub(super) struct BootstrapPipeWriter(OwnedHandle);

impl fmt::Debug for BootstrapPipeWriter {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("BootstrapPipeWriter(<redacted>)")
    }
}

impl BootstrapPipeWriter {
    /// Completes the entire bounded write before consuming and closing the sole
    /// parent bootstrap writer. No flush operation or duplicate is involved.
    pub(super) fn write_exact_and_close(
        self,
        bytes: &[u8],
        timeout: Duration,
    ) -> Result<(), ParentPipeError> {
        if bytes.len() != CHILD_BOOTSTRAP_FRAME_LEN {
            return Err(ParentPipeError::new("parent_pipe_bootstrap_length_invalid"));
        }
        write_exact_bounded(self.0.as_raw_handle().cast(), bytes, timeout)
    }
}

pub(super) struct ParentControlPipe(OwnedHandle);

impl fmt::Debug for ParentControlPipe {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("ParentControlPipe(<redacted>)")
    }
}

impl ParentControlPipe {
    pub(super) fn write_exact(
        &mut self,
        bytes: &[u8],
        timeout: Duration,
    ) -> Result<(), ParentPipeError> {
        write_exact_bounded(self.0.as_raw_handle().cast(), bytes, timeout)
    }

    pub(super) fn read_exact(
        &mut self,
        bytes: &mut [u8],
        timeout: Duration,
    ) -> Result<(), ParentPipeError> {
        read_exact_bounded(self.0.as_raw_handle().cast(), bytes, timeout)
    }
}

pub(super) struct ParentResultPipe(OwnedHandle);

impl fmt::Debug for ParentResultPipe {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("ParentResultPipe(<redacted>)")
    }
}

impl ParentResultPipe {
    pub(super) fn read_exact(
        &mut self,
        bytes: &mut [u8],
        timeout: Duration,
    ) -> Result<(), ParentPipeError> {
        read_exact_bounded(self.0.as_raw_handle().cast(), bytes, timeout)
    }
}

fn write_exact_bounded(
    handle: HANDLE,
    mut bytes: &[u8],
    timeout: Duration,
) -> Result<(), ParentPipeError> {
    let deadline = deadline(timeout)?;
    while !bytes.is_empty() {
        let chunk_len = bytes.len().min(u32::MAX as usize);
        let completion = overlapped_io(
            handle,
            bytes[..chunk_len].to_vec(),
            IoDirection::Write,
            deadline,
        )?;
        let transferred = completion.transferred as usize;
        if transferred == 0 || transferred > chunk_len {
            return Err(ParentPipeError::new("parent_pipe_exact_write_stalled"));
        }
        bytes = &bytes[transferred..];
    }
    Ok(())
}

fn read_exact_bounded(
    handle: HANDLE,
    mut bytes: &mut [u8],
    timeout: Duration,
) -> Result<(), ParentPipeError> {
    let deadline = deadline(timeout)?;
    while !bytes.is_empty() {
        let chunk_len = bytes.len().min(u32::MAX as usize);
        let completion = overlapped_io(handle, vec![0u8; chunk_len], IoDirection::Read, deadline)?;
        let transferred = completion.transferred as usize;
        if transferred == 0 || transferred > chunk_len {
            return Err(ParentPipeError::new("parent_pipe_exact_read_short"));
        }
        bytes[..transferred].copy_from_slice(&completion.buffer.as_slice()[..transferred]);
        let (_, remaining) = std::mem::take(&mut bytes).split_at_mut(transferred);
        bytes = remaining;
    }
    Ok(())
}

fn deadline(timeout: Duration) -> Result<Instant, ParentPipeError> {
    if timeout.is_zero() {
        return Err(ParentPipeError::new("parent_pipe_io_timeout"));
    }
    Instant::now()
        .checked_add(timeout)
        .ok_or_else(|| ParentPipeError::new("parent_pipe_io_timeout"))
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
) -> Result<IoCompletion, ParentPipeError> {
    let mut operation = PendingOverlappedOperation::new(buffer)?;
    let length = u32::try_from(operation.buffer.len())
        .map_err(|_| ParentPipeError::new("parent_pipe_io_length_invalid"))?;
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
        ERROR_BROKEN_PIPE => return Err(ParentPipeError::new("parent_pipe_broken")),
        _ => return Err(ParentPipeError::new("parent_pipe_io_start_failed")),
    }

    let remaining = deadline.saturating_duration_since(Instant::now());
    if remaining.is_zero() {
        return Err(cancel_error(
            cancel_and_settle_or_quarantine(handle, operation),
            "parent_pipe_io_timeout",
        ));
    }
    let wait_millis = remaining.as_millis().min(u128::from(u32::MAX - 1)).max(1) as u32;
    let wait = unsafe { WaitForSingleObject(operation.event.as_raw_handle().cast(), wait_millis) };
    if wait != WAIT_OBJECT_0 {
        return Err(cancel_error(
            cancel_and_settle_or_quarantine(handle, operation),
            if wait == WAIT_TIMEOUT {
                "parent_pipe_io_timeout"
            } else {
                "parent_pipe_io_wait_failed"
            },
        ));
    }

    transferred = 0;
    if unsafe { GetOverlappedResult(handle, operation.overlapped.as_mut(), &mut transferred, 0) }
        == 0
    {
        return Err(match unsafe { GetLastError() } {
            ERROR_BROKEN_PIPE => ParentPipeError::new("parent_pipe_broken"),
            ERROR_OPERATION_ABORTED => ParentPipeError::new("parent_pipe_io_cancelled"),
            _ => ParentPipeError::new("parent_pipe_io_complete_failed"),
        });
    }
    Ok(IoCompletion {
        transferred,
        buffer: operation.buffer,
    })
}

/// Cancellation is bounded. If the kernel does not report a terminal state in
/// the settlement window, the stable heap allocation and its I/O buffer are
/// intentionally quarantined for process lifetime. That exceptional leak is
/// preferable to returning while the kernel still references caller memory.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum CancelDisposition {
    Settled,
    Quarantined,
}

fn cancel_error(disposition: CancelDisposition, settled_code: &'static str) -> ParentPipeError {
    ParentPipeError::new(match disposition {
        CancelDisposition::Settled => settled_code,
        CancelDisposition::Quarantined => "parent_pipe_io_quarantined",
    })
}

fn cancel_and_settle_or_quarantine(
    handle: HANDLE,
    mut operation: PendingOverlappedOperation,
) -> CancelDisposition {
    unsafe {
        CancelIoEx(handle, operation.overlapped.as_ref());
    }
    if unsafe {
        WaitForSingleObject(
            operation.event.as_raw_handle().cast(),
            PIPE_CANCEL_SETTLE_MILLIS,
        )
    } == WAIT_OBJECT_0
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
    fn new(buffer: Vec<u8>) -> Result<Self, ParentPipeError> {
        let event = unsafe { CreateEventW(ptr::null(), 1, 0, ptr::null()) };
        if event.is_null() {
            return Err(ParentPipeError::new("parent_pipe_io_event_create_failed"));
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
        self.0.as_slice()
    }

    fn zeroize(&mut self) {
        for byte in &mut self.0 {
            unsafe {
                ptr::write_volatile(byte, 0);
            }
        }
        compiler_fence(Ordering::SeqCst);
    }
}

impl Drop for SensitiveIoBuffer {
    fn drop(&mut self) {
        self.zeroize();
    }
}

fn connect_server_after_client(handle: HANDLE, timeout: Duration) -> Result<(), ParentPipeError> {
    let deadline = deadline(timeout)?;
    let mut operation = PendingOverlappedOperation::new(Vec::new())?;
    if unsafe { ConnectNamedPipe(handle, operation.overlapped.as_mut()) } != 0 {
        return Ok(());
    }
    match unsafe { GetLastError() } {
        ERROR_PIPE_CONNECTED => Ok(()),
        ERROR_IO_PENDING => {
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return Err(cancel_error(
                    cancel_and_settle_or_quarantine(handle, operation),
                    "parent_pipe_connect_timeout",
                ));
            }
            let wait_millis = remaining.as_millis().min(u128::from(u32::MAX - 1)).max(1) as u32;
            let wait =
                unsafe { WaitForSingleObject(operation.event.as_raw_handle().cast(), wait_millis) };
            if wait != WAIT_OBJECT_0 {
                return Err(cancel_error(
                    cancel_and_settle_or_quarantine(handle, operation),
                    if wait == WAIT_TIMEOUT {
                        "parent_pipe_connect_timeout"
                    } else {
                        "parent_pipe_connect_wait_failed"
                    },
                ));
            }
            let mut transferred = 0u32;
            if unsafe {
                GetOverlappedResult(handle, operation.overlapped.as_mut(), &mut transferred, 0)
            } == 0
            {
                return Err(ParentPipeError::new("parent_pipe_connect_failed"));
            }
            Ok(())
        }
        _ => Err(ParentPipeError::new("parent_pipe_connect_failed")),
    }
}

fn handle_is_valid(handle: HANDLE) -> bool {
    if handle.is_null() || handle == INVALID_HANDLE_VALUE {
        return false;
    }
    let mut flags = 0u32;
    (unsafe { GetHandleInformation(handle, &mut flags) }) != 0
}

fn file_time_value(value: FILETIME) -> u64 {
    (u64::from(value.dwHighDateTime) << 32) | u64::from(value.dwLowDateTime)
}

fn handles_alias(left: HANDLE, right: HANDLE) -> bool {
    (unsafe { CompareObjectHandles(left, right) }) != 0
}

fn wide_null(value: &str) -> Vec<u16> {
    std::ffi::OsStr::new(value)
        .encode_wide()
        .chain(std::iter::once(0))
        .collect()
}

fn encode_hex(value: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(value.len() * 2);
    for byte in value {
        output.push(DIGITS[(byte >> 4) as usize] as char);
        output.push(DIGITS[(byte & 0x0f) as usize] as char);
    }
    output
}

#[cfg(test)]
fn current_process_user_sid_string() -> Result<String, ParentPipeError> {
    let mut raw_token = null_mut();
    if unsafe { OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut raw_token) } == 0 {
        return Err(ParentPipeError::new("parent_pipe_test_token_open_failed"));
    }
    let token = unsafe { OwnedHandle::from_raw_handle(raw_token as RawHandle) };
    let mut required = 0u32;
    unsafe {
        windows_sys::Win32::Foundation::SetLastError(ERROR_SUCCESS);
    }
    if unsafe {
        GetTokenInformation(
            token.as_raw_handle().cast(),
            TokenUser,
            null_mut(),
            0,
            &mut required,
        )
    } != 0
        || unsafe { GetLastError() } != ERROR_INSUFFICIENT_BUFFER
        || required < size_of::<TOKEN_USER>() as u32
        || required > 1024 * 1024
    {
        return Err(ParentPipeError::new("parent_pipe_test_token_query_failed"));
    }
    let word_size = size_of::<usize>();
    let word_count = (required as usize)
        .checked_add(word_size - 1)
        .ok_or_else(|| ParentPipeError::new("parent_pipe_test_token_query_failed"))?
        / word_size;
    let mut buffer = vec![0usize; word_count];
    if unsafe {
        GetTokenInformation(
            token.as_raw_handle().cast(),
            TokenUser,
            buffer.as_mut_ptr().cast(),
            required,
            &mut required,
        )
    } == 0
    {
        return Err(ParentPipeError::new("parent_pipe_test_token_query_failed"));
    }
    let token_user = unsafe { &*buffer.as_ptr().cast::<TOKEN_USER>() };
    if token_user.User.Sid.is_null() || unsafe { IsValidSid(token_user.User.Sid) } == 0 {
        return Err(ParentPipeError::new("parent_pipe_test_token_query_failed"));
    }
    let mut text = null_mut::<u16>();
    if unsafe { ConvertSidToStringSidW(token_user.User.Sid, &mut text) } == 0 || text.is_null() {
        if !text.is_null() {
            unsafe {
                LocalFree(text.cast());
            }
        }
        return Err(ParentPipeError::new("parent_pipe_test_sid_convert_failed"));
    }
    let Some(length) = (0..256).find(|offset| unsafe { *text.add(*offset) } == 0) else {
        unsafe {
            LocalFree(text.cast());
        }
        return Err(ParentPipeError::new("parent_pipe_test_sid_convert_failed"));
    };
    let value = String::from_utf16(unsafe { std::slice::from_raw_parts(text, length) })
        .map_err(|_| ParentPipeError::new("parent_pipe_test_sid_convert_failed"));
    unsafe {
        LocalFree(text.cast());
    }
    value
}

#[cfg(test)]
pub(super) struct TestParentHandshakePipeFixture {
    pub(super) servers: ParentPipeServers,
    pub(super) closed_parent_copies: ParentClientCopiesClosed,
    pub(super) child_peers: TestChildPipePeers,
    pub(super) inherited_raw_handles: [usize; CHILD_STANDARD_HANDLE_SLOT_COUNT],
}

#[cfg(test)]
pub(super) struct TestChildPipePeers {
    bootstrap: OwnedHandle,
    control: OwnedHandle,
    result: OwnedHandle,
}

#[cfg(test)]
impl TestChildPipePeers {
    pub(super) fn read_bootstrap_exact(
        &mut self,
        bytes: &mut [u8],
        timeout: Duration,
    ) -> Result<(), ParentPipeError> {
        read_exact_bounded(self.bootstrap.as_raw_handle().cast(), bytes, timeout)
    }

    pub(super) fn read_control_exact(
        &mut self,
        bytes: &mut [u8],
        timeout: Duration,
    ) -> Result<(), ParentPipeError> {
        read_exact_bounded(self.control.as_raw_handle().cast(), bytes, timeout)
    }

    pub(super) fn write_control_exact(
        &mut self,
        bytes: &[u8],
        timeout: Duration,
    ) -> Result<(), ParentPipeError> {
        write_exact_bounded(self.control.as_raw_handle().cast(), bytes, timeout)
    }

    pub(super) fn write_result_exact(
        &mut self,
        bytes: &[u8],
        timeout: Duration,
    ) -> Result<(), ParentPipeError> {
        write_exact_bounded(self.result.as_raw_handle().cast(), bytes, timeout)
    }
}

#[cfg(test)]
pub(super) fn test_parent_handshake_pipe_fixture(
    role: ChildBootstrapRole,
    process_key: ProcessKey,
    primary_thread_source: TransportDigest,
    launch_attribute_source: TransportDigest,
) -> Result<TestParentHandshakePipeFixture, ParentPipeError> {
    let first = unsafe { CreateEventW(ptr::null(), 1, 0, ptr::null()) };
    let second = unsafe { CreateEventW(ptr::null(), 1, 0, ptr::null()) };
    if first.is_null() || second.is_null() {
        if !first.is_null() {
            unsafe {
                CloseHandle(first);
            }
        }
        if !second.is_null() {
            unsafe {
                CloseHandle(second);
            }
        }
        return Err(ParentPipeError::new(
            "parent_pipe_test_exclusion_create_failed",
        ));
    }
    let first = unsafe { OwnedHandle::from_raw_handle(first as RawHandle) };
    let second = unsafe { OwnedHandle::from_raw_handle(second as RawHandle) };
    let exclusions =
        ParentHandleExclusions::new(first.as_raw_handle().cast(), second.as_raw_handle().cast())?;
    let security = ParentPipeSecuritySpec::for_test_current_process()?;
    let set = ParentPipeSet::create(&security, &exclusions)?;
    let (servers, lease) = set.take_inherited_client_handles();
    let inherited = lease.inherited_raw_handles();
    // These duplicates exist only so sibling unit tests can simulate peer I/O
    // without starting a process. Consequently this fixture is transport-only
    // evidence and must never be cited as proof that a real parent retained no
    // client copy after process creation.
    let child_peers = TestChildPipePeers {
        bootstrap: duplicate_test_handle(inherited[0])?,
        control: duplicate_test_handle(inherited[1])?,
        result: duplicate_test_handle(inherited[2])?,
    };
    let inherited_raw_handles = inherited.map(|handle| handle as usize);
    let created_child = CreatedSuspendedChildClosureBinding::for_test(
        role,
        process_key,
        primary_thread_source,
        launch_attribute_source,
    )?;
    let closed_parent_copies = lease.close_parent_copies_after_create(created_child)?;
    Ok(TestParentHandshakePipeFixture {
        servers,
        closed_parent_copies,
        child_peers,
        inherited_raw_handles,
    })
}

#[cfg(test)]
fn duplicate_test_handle(source: HANDLE) -> Result<OwnedHandle, ParentPipeError> {
    let process = unsafe { GetCurrentProcess() };
    let mut duplicate = null_mut();
    if unsafe {
        DuplicateHandle(
            process,
            source,
            process,
            &mut duplicate,
            0,
            0,
            DUPLICATE_SAME_ACCESS,
        )
    } == 0
        || duplicate.is_null()
    {
        return Err(ParentPipeError::new("parent_pipe_test_duplicate_failed"));
    }
    Ok(unsafe { OwnedHandle::from_raw_handle(duplicate as RawHandle) })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{env, fs, process::Command as TestCommand, thread};
    use windows_sys::{
        Wdk::{
            Foundation::{NtQueryObject, ObjectBasicInformation},
            Storage::FileSystem::{
                FileModeInformation, NtQueryInformationFile, FILE_MODE_INFORMATION,
                FILE_SYNCHRONOUS_IO_ALERT, FILE_SYNCHRONOUS_IO_NONALERT,
            },
        },
        Win32::{
            Foundation::SetHandleInformation,
            Security::{ImpersonateSelf, RevertToSelf, SecurityImpersonation, TOKEN_IMPERSONATE},
            Storage::FileSystem::GetFileType,
            System::{
                Threading::{GetCurrentProcess, GetCurrentThread, OpenThreadToken, SetThreadToken},
                IO::IO_STATUS_BLOCK,
            },
        },
    };

    struct RevertImpersonation;

    impl Drop for RevertImpersonation {
        fn drop(&mut self) {
            assert_ne!(unsafe { RevertToSelf() }, 0);
        }
    }

    struct TestExclusionHandles {
        job: OwnedHandle,
        completion: OwnedHandle,
    }

    struct TestSingleHandleAttributeList {
        storage: Vec<usize>,
        inherited_handle: Box<[HANDLE; 1]>,
        initialized: bool,
    }

    impl TestSingleHandleAttributeList {
        fn new(handle: HANDLE) -> Self {
            assert!(handle_is_valid(handle));
            assert_ne!(handle_flags(handle) & HANDLE_FLAG_INHERIT, 0);

            let mut required = 0usize;
            unsafe {
                windows_sys::Win32::Foundation::SetLastError(ERROR_SUCCESS);
            }
            assert_eq!(
                unsafe { InitializeProcThreadAttributeList(null_mut(), 1, 0, &mut required) },
                0
            );
            assert_eq!(unsafe { GetLastError() }, ERROR_INSUFFICIENT_BUFFER);
            assert!(required > 0 && required <= 1024 * 1024);

            let word_size = size_of::<usize>();
            let word_count = required
                .checked_add(word_size - 1)
                .expect("bounded attribute-list size")
                / word_size;
            let mut value = Self {
                storage: vec![0usize; word_count],
                inherited_handle: Box::new([handle]),
                initialized: false,
            };
            let raw_list = value.raw();
            assert_ne!(
                unsafe { InitializeProcThreadAttributeList(raw_list, 1, 0, &mut required) },
                0
            );
            value.initialized = true;
            let raw_list = value.raw();
            let inherited_handle = value.inherited_handle.as_ptr();
            assert_ne!(
                unsafe {
                    UpdateProcThreadAttribute(
                        raw_list,
                        0,
                        PROC_THREAD_ATTRIBUTE_HANDLE_LIST as usize,
                        inherited_handle.cast(),
                        size_of::<HANDLE>(),
                        null_mut(),
                        ptr::null(),
                    )
                },
                0
            );
            value
        }

        fn raw(&mut self) -> LPPROC_THREAD_ATTRIBUTE_LIST {
            self.storage.as_mut_ptr().cast()
        }
    }

    impl Drop for TestSingleHandleAttributeList {
        fn drop(&mut self) {
            if self.initialized {
                unsafe {
                    DeleteProcThreadAttributeList(self.storage.as_mut_ptr().cast());
                }
                self.initialized = false;
            }
        }
    }

    struct TestSuspendedChild {
        process: OwnedHandle,
        primary_thread: OwnedHandle,
        process_id: u32,
        primary_thread_id: u32,
        cleaned: bool,
    }

    impl TestSuspendedChild {
        fn create(attributes: &mut TestSingleHandleAttributeList) -> Self {
            let application = std::env::current_exe()
                .expect("current test executable")
                .as_os_str()
                .encode_wide()
                .chain(std::iter::once(0))
                .collect::<Vec<_>>();
            let mut startup = unsafe { zeroed::<STARTUPINFOEXW>() };
            startup.StartupInfo.cb = size_of::<STARTUPINFOEXW>() as u32;
            startup.lpAttributeList = attributes.raw();
            let mut process_information = unsafe { zeroed::<PROCESS_INFORMATION>() };
            assert_ne!(
                unsafe {
                    CreateProcessW(
                        application.as_ptr(),
                        null_mut(),
                        ptr::null(),
                        ptr::null(),
                        1,
                        CREATE_SUSPENDED | EXTENDED_STARTUPINFO_PRESENT | CREATE_NO_WINDOW,
                        ptr::null(),
                        ptr::null(),
                        &startup.StartupInfo,
                        &mut process_information,
                    )
                },
                0,
                "create a hidden suspended child"
            );
            let child = Self {
                process: unsafe {
                    OwnedHandle::from_raw_handle(process_information.hProcess as RawHandle)
                },
                primary_thread: unsafe {
                    OwnedHandle::from_raw_handle(process_information.hThread as RawHandle)
                },
                process_id: process_information.dwProcessId,
                primary_thread_id: process_information.dwThreadId,
                cleaned: false,
            };
            assert_ne!(child.process_id, 0);
            assert_ne!(child.primary_thread_id, 0);
            child
        }

        fn process_id(&self) -> u32 {
            self.process_id
        }

        fn process_handle(&self) -> HANDLE {
            self.process.as_raw_handle().cast()
        }

        fn primary_thread_handle(&self) -> HANDLE {
            self.primary_thread.as_raw_handle().cast()
        }

        fn primary_thread_id(&self) -> u32 {
            self.primary_thread_id
        }

        fn terminate_and_wait(mut self) {
            assert!(self.cleanup(), "suspended child cleanup must complete");
        }

        fn cleanup(&mut self) -> bool {
            if self.cleaned {
                return true;
            }
            let process = self.process.as_raw_handle().cast();
            match unsafe { WaitForSingleObject(process, 0) } {
                WAIT_OBJECT_0 => {
                    self.cleaned = true;
                    true
                }
                WAIT_TIMEOUT => {
                    if unsafe { TerminateProcess(process, 0x5646_0001) } == 0
                        && unsafe { WaitForSingleObject(process, 0) } != WAIT_OBJECT_0
                    {
                        return false;
                    }
                    self.cleaned =
                        unsafe { WaitForSingleObject(process, PIPE_DEFAULT_TIMEOUT_MILLIS) }
                            == WAIT_OBJECT_0;
                    self.cleaned
                }
                _ => false,
            }
        }
    }

    impl Drop for TestSuspendedChild {
        fn drop(&mut self) {
            let _ = self.cleanup();
        }
    }

    impl TestExclusionHandles {
        fn create() -> Self {
            let first = unsafe { CreateEventW(ptr::null(), 1, 0, ptr::null()) };
            let second = unsafe { CreateEventW(ptr::null(), 1, 0, ptr::null()) };
            assert!(!first.is_null());
            assert!(!second.is_null());
            Self {
                job: unsafe { OwnedHandle::from_raw_handle(first as RawHandle) },
                completion: unsafe { OwnedHandle::from_raw_handle(second as RawHandle) },
            }
        }

        fn exclusions(&self) -> ParentHandleExclusions {
            ParentHandleExclusions::from_borrowed(self.job.as_handle(), self.completion.as_handle())
                .unwrap()
        }
    }

    fn fixture() -> (ParentPipeSecuritySpec, TestExclusionHandles) {
        (
            ParentPipeSecuritySpec::for_test_current_process().unwrap(),
            TestExclusionHandles::create(),
        )
    }

    fn process_handle_count() -> u32 {
        let mut count = 0u32;
        assert_ne!(
            unsafe { GetProcessHandleCount(GetCurrentProcess(), &mut count) },
            0
        );
        count
    }

    fn handle_flags(handle: HANDLE) -> u32 {
        let mut flags = 0u32;
        assert_ne!(unsafe { GetHandleInformation(handle, &mut flags) }, 0);
        flags
    }

    fn granted_access(handle: HANDLE) -> u32 {
        let mut information = unsafe { zeroed::<PublicObjectBasicInformation>() };
        let mut returned = 0u32;
        let status = unsafe {
            NtQueryObject(
                handle,
                ObjectBasicInformation,
                (&mut information as *mut PublicObjectBasicInformation).cast(),
                size_of::<PublicObjectBasicInformation>() as u32,
                &mut returned,
            )
        };
        assert!(status >= 0);
        assert!(returned >= (size_of::<u32>() * 2) as u32);
        information.granted_access
    }

    fn file_mode(handle: HANDLE) -> u32 {
        let mut status = unsafe { zeroed::<IO_STATUS_BLOCK>() };
        let mut mode = FILE_MODE_INFORMATION { Mode: u32::MAX };
        let result = unsafe {
            NtQueryInformationFile(
                handle,
                &mut status,
                (&mut mode as *mut FILE_MODE_INFORMATION).cast(),
                size_of::<FILE_MODE_INFORMATION>() as u32,
                FileModeInformation,
            )
        };
        assert!(result >= 0, "NtQueryInformationFile status={result}");
        mode.Mode
    }

    fn pipe_state(handle: HANDLE) -> u32 {
        let mut state = u32::MAX;
        assert_ne!(
            unsafe {
                GetNamedPipeHandleStateW(
                    handle,
                    &mut state,
                    ptr::null_mut(),
                    ptr::null_mut(),
                    ptr::null_mut(),
                    ptr::null_mut(),
                    0,
                )
            },
            0
        );
        state
    }

    #[repr(C)]
    #[derive(Clone, Copy)]
    struct PublicObjectBasicInformation {
        attributes: u32,
        granted_access: u32,
        handle_count: u32,
        pointer_count: u32,
        reserved: [u32; 10],
    }

    #[derive(Debug, PartialEq, Eq)]
    struct SecurityReadback {
        owner_sid: String,
        control: u16,
        aces: Vec<(u32, u8, String)>,
    }

    fn security_readback(handle: HANDLE) -> SecurityReadback {
        let mut descriptor: PSECURITY_DESCRIPTOR = null_mut();
        assert_eq!(
            unsafe {
                GetSecurityInfo(
                    handle,
                    SE_KERNEL_OBJECT,
                    OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
                    ptr::null_mut(),
                    ptr::null_mut(),
                    ptr::null_mut(),
                    ptr::null_mut(),
                    &mut descriptor,
                )
            },
            ERROR_SUCCESS
        );
        assert!(!descriptor.is_null());
        let _descriptor_owner = OwnedSecurityDescriptor(descriptor);
        descriptor_readback(descriptor)
    }

    fn descriptor_readback(descriptor: PSECURITY_DESCRIPTOR) -> SecurityReadback {
        let mut owner: PSID = null_mut();
        let mut owner_defaulted = 0i32;
        assert_ne!(
            unsafe { GetSecurityDescriptorOwner(descriptor, &mut owner, &mut owner_defaulted) },
            0
        );
        assert!(!owner.is_null());
        assert_eq!(owner_defaulted, 0);
        let mut dacl_present = 0i32;
        let mut dacl_defaulted = 0i32;
        let mut dacl: *mut ACL = null_mut();
        assert_ne!(
            unsafe {
                GetSecurityDescriptorDacl(
                    descriptor,
                    &mut dacl_present,
                    &mut dacl,
                    &mut dacl_defaulted,
                )
            },
            0
        );
        assert_ne!(dacl_present, 0);
        assert_eq!(dacl_defaulted, 0);
        assert!(!dacl.is_null());

        let mut control = 0u16;
        let mut revision = 0u32;
        assert_ne!(
            unsafe { GetSecurityDescriptorControl(descriptor, &mut control, &mut revision) },
            0
        );
        assert_ne!(revision, 0);

        let mut aces = Vec::new();
        for index in 0..unsafe { (*dacl).AceCount } {
            let mut raw_ace = null_mut();
            assert_ne!(unsafe { GetAce(dacl, u32::from(index), &mut raw_ace) }, 0);
            assert!(!raw_ace.is_null());
            let ace = unsafe { &*raw_ace.cast::<ACCESS_ALLOWED_ACE>() };
            assert_eq!(ace.Header.AceType, ACCESS_ALLOWED_ACE_TYPE as u8);
            let sid = ptr::addr_of!(ace.SidStart).cast_mut().cast();
            aces.push((ace.Mask, ace.Header.AceFlags, sid_to_string(sid)));
        }
        SecurityReadback {
            owner_sid: sid_to_string(owner),
            control,
            aces,
        }
    }

    fn sid_to_string(sid: PSID) -> String {
        assert!(!sid.is_null());
        assert_ne!(unsafe { IsValidSid(sid) }, 0);
        let mut text = null_mut::<u16>();
        assert_ne!(unsafe { ConvertSidToStringSidW(sid, &mut text) }, 0);
        assert!(!text.is_null());
        let length = (0..256)
            .find(|offset| unsafe { *text.add(*offset) } == 0)
            .expect("bounded SID text");
        let value = String::from_utf16(unsafe { std::slice::from_raw_parts(text, length) })
            .expect("valid SID UTF-16");
        unsafe {
            LocalFree(text.cast());
        }
        value
    }

    #[test]
    fn exact_three_pipe_shape_direction_access_identity_and_order_are_kernel_verified() {
        let (security, exclusions) = fixture();
        let set = ParentPipeSet::create(&security, &exclusions.exclusions()).unwrap();
        let servers = [
            set.bootstrap.server_raw(),
            set.control.server_raw(),
            set.result.server_raw(),
        ];
        let clients = [
            set.bootstrap.client_raw(),
            set.control.client_raw(),
            set.result.client_raw(),
        ];

        for (server, slot) in servers.iter().copied().zip(PipeSlot::ALL) {
            let readback = security_readback(server);
            assert_eq!(readback.owner_sid, security.owner_sid);
            assert_ne!(readback.control & SE_DACL_PROTECTED, 0);
            assert_eq!(
                readback.aces,
                vec![(
                    security.descriptor_access(slot),
                    0,
                    security.owner_sid.clone()
                )]
            );
        }

        for (index, server) in servers.into_iter().enumerate() {
            assert_eq!(unsafe { GetFileType(server) }, FILE_TYPE_PIPE);
            assert_eq!(handle_flags(server) & HANDLE_FLAG_INHERIT, 0);
            let access = granted_access(server);
            match index {
                0 => {
                    assert_ne!(access & FILE_WRITE_DATA, 0);
                    assert_eq!(access & FILE_READ_DATA, 0);
                }
                1 => {
                    assert_ne!(access & FILE_WRITE_DATA, 0);
                    assert_ne!(access & FILE_READ_DATA, 0);
                }
                2 => {
                    assert_eq!(access & FILE_WRITE_DATA, 0);
                    assert_ne!(access & FILE_READ_DATA, 0);
                }
                _ => unreachable!(),
            }
            let mut client_pid = 0u32;
            assert_ne!(
                unsafe { GetNamedPipeClientProcessId(server, &mut client_pid) },
                0
            );
            assert_eq!(client_pid, unsafe { GetCurrentProcessId() });
            assert_eq!(
                file_mode(server) & (FILE_SYNCHRONOUS_IO_ALERT | FILE_SYNCHRONOUS_IO_NONALERT),
                0
            );
        }

        let expected_access = PipeSlot::ALL.map(PipeSlot::client_acl_access);
        for (index, client) in clients.iter().copied().enumerate() {
            assert_eq!(unsafe { GetFileType(client) }, FILE_TYPE_PIPE);
            assert_ne!(handle_flags(client) & HANDLE_FLAG_INHERIT, 0);
            assert_eq!(granted_access(client), expected_access[index]);
            let mut server_pid = 0u32;
            assert_ne!(
                unsafe { GetNamedPipeServerProcessId(client, &mut server_pid) },
                0
            );
            assert_eq!(server_pid, unsafe { GetCurrentProcessId() });
            let mut pipe_flags = 0u32;
            assert_ne!(
                unsafe {
                    GetNamedPipeInfo(
                        client,
                        &mut pipe_flags,
                        ptr::null_mut(),
                        ptr::null_mut(),
                        ptr::null_mut(),
                    )
                },
                0,
                "client slot {index} cannot query pipe info"
            );
            assert_eq!(pipe_flags & PIPE_SERVER_END, 0);
            assert_eq!(
                file_mode(client) & (FILE_SYNCHRONOUS_IO_ALERT | FILE_SYNCHRONOUS_IO_NONALERT),
                0
            );
            assert_eq!(
                pipe_state(client) & (PIPE_READMODE_MESSAGE | PIPE_NOWAIT),
                0,
                "client slot {index} mode drift"
            );
            assert!(clients[..index]
                .iter()
                .all(|prior| !handles_alias(*prior, client)));
            assert!(exclusions
                .exclusions()
                .all()
                .iter()
                .all(|excluded| !handles_alias(*excluded, client)));
        }

        let (_, lease) = set.take_inherited_client_handles();
        assert_eq!(lease.inherited_raw_handles(), clients);
        assert_eq!(
            lease
                .inherited_borrowed_handles()
                .map(|handle| handle.as_raw_handle().cast()),
            clients
        );
    }

    #[test]
    fn inherited_client_keeps_parent_creator_process_id_after_parent_copy_closes() {
        let security = ParentPipeSecuritySpec::for_test_current_process().unwrap();
        let mut nonce = [0u8; PIPE_NAME_RANDOM_BYTES];
        getrandom::fill(&mut nonce).expect("secure test pipe name");
        let pipe_name = format!(
            "{PIPE_NAME_PREFIX}{}.client-creator-regression",
            encode_hex(&nonce)
        );
        let pipe = ConnectedPipe::create(&security, PipeSlot::Control, &pipe_name).unwrap();
        let (server, client) = pipe.into_parts();
        let server_handle = server.as_raw_handle().cast();
        let client_handle = client.as_raw_handle().cast();
        let parent_process_id = unsafe { GetCurrentProcessId() };
        let mut creator_before_inheritance = 0u32;
        assert_ne!(
            unsafe { GetNamedPipeClientProcessId(server_handle, &mut creator_before_inheritance) },
            0
        );
        assert_eq!(creator_before_inheritance, parent_process_id);

        let mut attributes = TestSingleHandleAttributeList::new(client_handle);
        let child = TestSuspendedChild::create(&mut attributes);
        assert_ne!(child.process_id(), parent_process_id);
        drop(attributes);
        drop(client);

        // This query reports the process that created the client endpoint. An
        // inherited handle does not turn it into proof of the current holder.
        let mut creator_after_parent_close = 0u32;
        assert_ne!(
            unsafe { GetNamedPipeClientProcessId(server_handle, &mut creator_after_parent_close) },
            0
        );
        assert_eq!(creator_after_parent_close, parent_process_id);
        assert_ne!(creator_after_parent_close, child.process_id());

        child.terminate_and_wait();
        drop(server);
    }

    #[test]
    fn held_suspended_create_binding_requires_exact_live_process_and_primary_thread() {
        let event = unsafe { CreateEventW(ptr::null(), 1, 0, ptr::null()) };
        assert!(!event.is_null());
        let event = unsafe { OwnedHandle::from_raw_handle(event as RawHandle) };
        assert_ne!(
            unsafe {
                SetHandleInformation(
                    event.as_raw_handle().cast(),
                    HANDLE_FLAG_INHERIT,
                    HANDLE_FLAG_INHERIT,
                )
            },
            0
        );
        let mut attributes = TestSingleHandleAttributeList::new(event.as_raw_handle().cast());
        let child = TestSuspendedChild::create(&mut attributes);
        let binding = CreatedSuspendedChildClosureBinding::from_held_suspended_create_result(
            ChildBootstrapRole::LifecycleDriver,
            child.process_handle(),
            child.primary_thread_handle(),
            child.process_id(),
            child.primary_thread_id(),
            [0x61; 32],
        )
        .unwrap();
        assert_eq!(binding.role, ChildBootstrapRole::LifecycleDriver);
        assert_eq!(binding.process_key.pid, child.process_id());
        assert_ne!(binding.process_key.creation_time, 0);
        binding.validate().unwrap();

        for (process, thread, process_id, thread_id, source) in [
            (
                child.process_handle(),
                child.primary_thread_handle(),
                child.process_id().wrapping_add(1),
                child.primary_thread_id(),
                [0x61; 32],
            ),
            (
                child.process_handle(),
                child.primary_thread_handle(),
                child.process_id(),
                child.primary_thread_id().wrapping_add(1),
                [0x61; 32],
            ),
            (
                event.as_raw_handle().cast(),
                child.primary_thread_handle(),
                child.process_id(),
                child.primary_thread_id(),
                [0x61; 32],
            ),
            (
                child.process_handle(),
                child.primary_thread_handle(),
                child.process_id(),
                child.primary_thread_id(),
                [0; 32],
            ),
        ] {
            assert_eq!(
                CreatedSuspendedChildClosureBinding::from_held_suspended_create_result(
                    ChildBootstrapRole::LifecycleDriver,
                    process,
                    thread,
                    process_id,
                    thread_id,
                    source,
                )
                .unwrap_err()
                .code(),
                "parent_created_suspended_child_handle_invalid"
            );
        }

        assert_ne!(unsafe { ImpersonateSelf(SecurityImpersonation) }, 0);
        let guard = RevertImpersonation;
        let mut raw_token = null_mut();
        assert_ne!(
            unsafe {
                OpenThreadToken(
                    GetCurrentThread(),
                    TOKEN_QUERY | TOKEN_IMPERSONATE,
                    1,
                    &mut raw_token,
                )
            },
            0
        );
        let token = unsafe { OwnedHandle::from_raw_handle(raw_token as RawHandle) };
        drop(guard);
        let primary_thread = child.primary_thread_handle();
        assert_ne!(
            unsafe { SetThreadToken(&primary_thread, token.as_raw_handle().cast()) },
            0
        );
        assert_eq!(
            CreatedSuspendedChildClosureBinding::from_held_suspended_create_result(
                ChildBootstrapRole::LifecycleDriver,
                child.process_handle(),
                child.primary_thread_handle(),
                child.process_id(),
                child.primary_thread_id(),
                [0x61; 32],
            )
            .unwrap_err()
            .code(),
            "protected_child_thread_impersonation_token_forbidden"
        );
        assert_ne!(unsafe { SetThreadToken(&primary_thread, null_mut()) }, 0);

        child.terminate_and_wait();
    }

    #[test]
    fn partial_creation_faults_and_normal_drop_are_handle_leak_bounded() {
        const ISOLATED_ENV: &str = "VRCFORGE_ISOLATED_PIPE_HANDLE_TEST";
        if env::var_os(ISOLATED_ENV).is_none() {
            let status = TestCommand::new(env::current_exe().unwrap())
                .arg("--exact")
                .arg(
                    "primitive_evidence_authority_supervisor::native_windows::child_transport::tests::partial_creation_faults_and_normal_drop_are_handle_leak_bounded",
                )
                .arg("--nocapture")
                .env(ISOLATED_ENV, "1")
                .status()
                .unwrap();
            assert!(status.success(), "isolated pipe handle leak test failed");
            return;
        }
        let (security, exclusions) = fixture();
        let exercise = || {
            for fault in 0..=3 {
                let error =
                    ParentPipeSet::create_inner(&security, &exclusions.exclusions(), Some(fault))
                        .unwrap_err();
                assert_eq!(error.code(), "parent_pipe_test_create_fault");
            }
            for _ in 0..16 {
                drop(ParentPipeSet::create(&security, &exclusions.exclusions()).unwrap());
            }
        };

        // Exclude one-time process-global API initialization from the ownership
        // measurement. Repeating the exact fault and success matrix still
        // detects any handle retained by each construction cycle.
        exercise();
        let baseline = process_handle_count();
        exercise();
        let after = process_handle_count();
        assert!(after <= baseline.saturating_add(2), "{baseline} -> {after}");
    }

    #[test]
    fn client_lease_close_consumes_all_parent_copies() {
        let (security, exclusions) = fixture();
        let set = ParentPipeSet::create(&security, &exclusions.exclusions()).unwrap();
        let (servers, lease) = set.take_inherited_client_handles();
        let raw = lease.inherited_raw_handles();
        let created_child = CreatedSuspendedChildClosureBinding::for_test(
            ChildBootstrapRole::LifecycleDriver,
            ProcessKey {
                pid: 0x1201,
                creation_time: 0x1202,
            },
            [0x31; 32],
            [0x32; 32],
        )
        .unwrap();
        let closed = lease
            .close_parent_copies_after_create(created_child)
            .unwrap();
        assert_eq!(format!("{closed:?}"), "ParentClientCopiesClosed");
        closed.validate().unwrap();
        assert_eq!(closed.role(), ChildBootstrapRole::LifecycleDriver);
        assert_eq!(closed.process_key().pid, 0x1201);
        assert_eq!(closed.process_key().creation_time, 0x1202);
        assert_ne!(
            closed.primary_thread_binding().as_bytes(),
            closed.launch_attribute_binding().as_bytes()
        );
        for handle in raw {
            let mut flags = 0u32;
            assert_eq!(unsafe { GetHandleInformation(handle, &mut flags) }, 0);
        }
        let mut tampered = closed;
        tampered.closure_binding_digest[0] ^= 1;
        assert_eq!(
            tampered.validate().unwrap_err().code(),
            "parent_pipe_client_closure_binding_invalid"
        );
        drop(servers);
    }

    #[test]
    fn bootstrap_exact_write_completes_before_the_only_writer_closes() {
        let (security, exclusions) = fixture();
        let set = ParentPipeSet::create(&security, &exclusions.exclusions()).unwrap();
        let (servers, lease) = set.take_inherited_client_handles();
        let handles = lease.inherited_raw_handles();
        let bootstrap_reader = handles[0] as usize;
        let payload = [0x5au8; CHILD_BOOTSTRAP_FRAME_LEN];
        thread::scope(|scope| {
            let reader = scope.spawn(move || {
                let mut received = [0u8; CHILD_BOOTSTRAP_FRAME_LEN];
                read_exact_bounded(
                    bootstrap_reader as HANDLE,
                    &mut received,
                    Duration::from_secs(2),
                )
                .unwrap();
                let mut trailing = [0u8; 1];
                let error = read_exact_bounded(
                    bootstrap_reader as HANDLE,
                    &mut trailing,
                    Duration::from_secs(2),
                )
                .unwrap_err();
                (received, error)
            });
            servers
                .bootstrap
                .write_exact_and_close(&payload, Duration::from_secs(2))
                .unwrap();
            let (received, error) = reader.join().unwrap();
            assert_eq!(received, payload);
            assert_eq!(error.code(), "parent_pipe_broken");
        });
        lease.close_after_create_failure();
    }

    #[test]
    fn bootstrap_writer_rejects_every_noncanonical_length_and_closes() {
        for length in [CHILD_BOOTSTRAP_FRAME_LEN - 1, CHILD_BOOTSTRAP_FRAME_LEN + 1] {
            let (security, exclusions) = fixture();
            let set = ParentPipeSet::create(&security, &exclusions.exclusions()).unwrap();
            let (servers, lease) = set.take_inherited_client_handles();
            let error = servers
                .bootstrap
                .write_exact_and_close(&vec![0x31; length], Duration::from_secs(2))
                .unwrap_err();
            assert_eq!(error.code(), "parent_pipe_bootstrap_length_invalid");
            let mut trailing = [0u8; 1];
            assert_eq!(
                read_exact_bounded(
                    lease.inherited_raw_handles()[0],
                    &mut trailing,
                    Duration::from_secs(2),
                )
                .unwrap_err()
                .code(),
                "parent_pipe_broken"
            );
        }
    }

    #[test]
    fn pending_read_times_out_cancels_and_does_not_poison_the_pipe() {
        let (security, exclusions) = fixture();
        let set = ParentPipeSet::create(&security, &exclusions.exclusions()).unwrap();
        let (mut servers, lease) = set.take_inherited_client_handles();
        let mut byte = [0u8; 1];
        let error = servers
            .result
            .read_exact(&mut byte, Duration::from_millis(25))
            .unwrap_err();
        assert_eq!(error.code(), "parent_pipe_io_timeout");
        write_exact_bounded(
            lease.inherited_raw_handles()[2],
            &[0x71],
            Duration::from_secs(2),
        )
        .unwrap();
        servers
            .result
            .read_exact(&mut byte, Duration::from_secs(2))
            .unwrap();
        assert_eq!(byte, [0x71]);
    }

    #[test]
    fn control_pipe_keeps_independent_bounded_duplex_io() {
        let (security, exclusions) = fixture();
        let set = ParentPipeSet::create(&security, &exclusions.exclusions()).unwrap();
        let (mut servers, lease) = set.take_inherited_client_handles();
        let control = lease.inherited_raw_handles()[1];

        servers
            .control
            .write_exact(&[0x11, 0x12], Duration::from_secs(2))
            .unwrap();
        let mut child_readback = [0u8; 2];
        read_exact_bounded(control, &mut child_readback, Duration::from_secs(2)).unwrap();
        assert_eq!(child_readback, [0x11, 0x12]);

        write_exact_bounded(control, &[0x21, 0x22], Duration::from_secs(2)).unwrap();
        let mut parent_readback = [0u8; 2];
        servers
            .control
            .read_exact(&mut parent_readback, Duration::from_secs(2))
            .unwrap();
        assert_eq!(parent_readback, [0x21, 0x22]);
    }

    #[test]
    fn completed_io_buffers_use_the_volatile_zeroization_path() {
        let mut buffer = SensitiveIoBuffer(vec![0x5a; CHILD_BOOTSTRAP_FRAME_LEN]);
        buffer.zeroize();
        assert!(buffer.as_slice().iter().all(|byte| *byte == 0));
        let source = fs::read_to_string(file!()).unwrap();
        assert!(source.contains("ptr::write_volatile(byte, 0)"));
        assert!(source.contains("compiler_fence(Ordering::SeqCst)"));
        assert!(source.contains("parent_pipe_io_quarantined"));
        assert!(ParentPipeError::new("parent_pipe_io_quarantined").requires_session_containment());
        assert!(!ParentPipeError::new("parent_pipe_io_timeout").requires_session_containment());
    }

    #[test]
    fn debug_and_source_contract_hide_names_handles_and_forbid_flushes() {
        let (security, exclusions) = fixture();
        let set = ParentPipeSet::create(&security, &exclusions.exclusions()).unwrap();
        let debug = format!("{set:?} {security:?} {:?}", exclusions.exclusions());
        assert!(!debug.contains(PIPE_NAME_PREFIX));
        for handle in [
            set.bootstrap.server_raw(),
            set.bootstrap.client_raw(),
            set.control.server_raw(),
            set.control.client_raw(),
            set.result.server_raw(),
            set.result.client_raw(),
        ] {
            assert!(!debug.contains(&(handle as usize).to_string()));
        }

        let source = fs::read_to_string(file!()).unwrap();
        assert!(!source.contains(concat!("FlushFile", "Buffers")));
        assert!(source.contains("FILE_FLAG_FIRST_PIPE_INSTANCE | FILE_FLAG_OVERLAPPED"));
        assert!(source.contains("const CLIENT_COMMON_FLAGS: u32 = FILE_FLAG_OVERLAPPED"));
        assert!(source.contains("PIPE_REJECT_REMOTE_CLIENTS"));
        assert!(source.contains("PIPE_TYPE_BYTE | PIPE_READMODE_BYTE"));
    }

    #[test]
    fn production_security_input_is_generation_bound_and_has_no_broad_principals() {
        let spec = ParentPipeSecuritySpec::from_parts(
            [0x81; 32],
            [0x82; 32],
            LOCAL_SYSTEM_SID.to_owned(),
            false,
        )
        .unwrap();
        let bootstrap = format!(
            "O:SYD:P(A;;0x{:08x};;;SY)(A;;0x{:08x};;;{})",
            PipeSlot::Bootstrap.client_acl_access(),
            PipeSlot::Bootstrap.client_acl_access(),
            authority_service_sid()
        );
        assert!(!bootstrap.contains(";;;BA)"));
        assert!(!bootstrap.contains(";;;WD)"));
        assert!(!bootstrap.contains(";;;AU)"));
        let descriptor = spec.descriptor_for(PipeSlot::Bootstrap).unwrap();
        let readback = descriptor_readback(descriptor.raw());
        assert_eq!(readback.owner_sid, LOCAL_SYSTEM_SID);
        assert_ne!(readback.control & SE_DACL_PROTECTED, 0);
        assert_eq!(
            readback.aces,
            vec![
                (
                    PipeSlot::Bootstrap.client_acl_access(),
                    0,
                    LOCAL_SYSTEM_SID.to_owned(),
                ),
                (
                    PipeSlot::Bootstrap.client_acl_access(),
                    0,
                    authority_service_sid().to_owned(),
                ),
            ]
        );
        let mut drifted = spec.clone();
        drifted.authority_generation_digest[0] ^= 0x80;
        assert_eq!(
            drifted.validate().unwrap_err().code(),
            "parent_pipe_security_spec_invalid"
        );
    }

    #[test]
    fn exclusions_reject_invalid_and_aliased_kernel_objects() {
        let handles = TestExclusionHandles::create();
        let raw = handles.job.as_raw_handle().cast();
        assert_eq!(
            ParentHandleExclusions::new(raw, raw).unwrap_err().code(),
            "parent_pipe_excluded_handle_set_invalid"
        );
        assert_eq!(
            ParentHandleExclusions::new(ptr::null_mut(), handles.completion.as_raw_handle().cast())
                .unwrap_err()
                .code(),
            "parent_pipe_excluded_handle_set_invalid"
        );
    }

    #[test]
    fn inherit_flag_tamper_is_detected() {
        let (security, exclusions) = fixture();
        let set = ParentPipeSet::create(&security, &exclusions.exclusions()).unwrap();
        assert_ne!(
            unsafe { SetHandleInformation(set.bootstrap.client_raw(), HANDLE_FLAG_INHERIT, 0,) },
            0
        );
        assert_eq!(
            set.bootstrap.validate_handle_flags().unwrap_err().code(),
            "parent_pipe_inherit_contract_invalid"
        );
    }

    #[test]
    fn broken_pipe_is_never_promoted_to_successful_eof() {
        let (security, exclusions) = fixture();
        let set = ParentPipeSet::create(&security, &exclusions.exclusions()).unwrap();
        let (mut servers, lease) = set.take_inherited_client_handles();
        lease.close_after_create_failure();
        let mut bytes = [0u8; 336];
        assert_eq!(
            servers
                .result
                .read_exact(&mut bytes, Duration::from_secs(2))
                .unwrap_err()
                .code(),
            "parent_pipe_broken"
        );
    }

    #[test]
    fn raw_close_probe_does_not_close_foreign_handles() {
        let event = unsafe { CreateEventW(ptr::null(), 1, 0, ptr::null()) };
        assert!(!event.is_null());
        assert_ne!(unsafe { CloseHandle(event) }, 0);
    }
}

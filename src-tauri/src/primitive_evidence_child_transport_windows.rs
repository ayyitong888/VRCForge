//! Fixed inherited standard-handle contract for protected child entry points.
//!
//! This module validates transport shape only. It cannot manufacture
//! `ChildBootstrapExpectations`, authenticate the service peer, or start a
//! child runtime. A later authenticated expectation source must consume the
//! non-clone validation witness and authenticate its kernel-reported server.

#![cfg(windows)]
#![cfg_attr(not(test), allow(dead_code))]

use crate::primitive_evidence_child_protocol::{
    child_role_capability_schema, BootstrapDigest, ChildBootstrapRole,
    ChildRoleCapabilitySlotBinding, ChildStandardHandlePurpose, RoleCapabilitySetBinding,
    RoleRawHandleListDigest, CHILD_BOOTSTRAP_FRAME_LEN, CHILD_STANDARD_HANDLE_SLOT_COUNT,
};
use sha2::{Digest as Sha2Digest, Sha256};
use std::{
    fmt,
    mem::size_of,
    os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle, RawHandle},
    ptr,
};
use windows_sys::Wdk::{
    Foundation::{NtQueryObject, ObjectBasicInformation},
    Storage::FileSystem::{
        FileModeInformation, FilePipeInformation, NtQueryInformationFile, FILE_MODE_INFORMATION,
        FILE_PIPE_BYTE_STREAM_MODE, FILE_PIPE_INFORMATION, FILE_PIPE_QUEUE_OPERATION,
        FILE_SYNCHRONOUS_IO_ALERT, FILE_SYNCHRONOUS_IO_NONALERT,
    },
};
use windows_sys::Win32::{
    Foundation::{
        CompareObjectHandles, GetHandleInformation, SetHandleInformation, HANDLE,
        HANDLE_FLAG_INHERIT,
    },
    Storage::FileSystem::{
        GetFileType, FILE_GENERIC_READ, FILE_GENERIC_WRITE, FILE_READ_ATTRIBUTES, FILE_TYPE_PIPE,
    },
    System::{
        Console::{
            GetStdHandle, SetStdHandle, STD_ERROR_HANDLE, STD_INPUT_HANDLE, STD_OUTPUT_HANDLE,
        },
        Pipes::{
            GetNamedPipeInfo, GetNamedPipeServerProcessId, PIPE_REJECT_REMOTE_CLIENTS,
            PIPE_SERVER_END, PIPE_TYPE_MESSAGE,
        },
        Threading::{GetCurrentProcessId, GetStartupInfoW, STARTF_USESTDHANDLES, STARTUPINFOW},
        IO::IO_STATUS_BLOCK,
    },
};

const SLOT_CAPABILITY_DOMAIN: &[u8] = b"vrcforge-child-standard-handle-slot-v1\0";
const STARTUP_CONTRACT_INVALID: &str = "child_standard_handles_startup_contract_invalid";
const STANDARD_HANDLE_INVALID: &str = "child_standard_handle_invalid";
const STANDARD_HANDLE_SLOT_MISMATCH: &str = "child_standard_handle_slot_mismatch";
const STANDARD_HANDLE_ALIAS: &str = "child_standard_handle_alias";
const STANDARD_HANDLE_INHERIT_MISSING: &str = "child_standard_handle_inherit_missing";
const STANDARD_HANDLE_INHERIT_CLEAR_FAILED: &str = "child_standard_handle_inherit_clear_failed";
const STANDARD_HANDLE_INHERIT_CLEAR_UNVERIFIED: &str =
    "child_standard_handle_inherit_clear_unverified";
const STANDARD_HANDLE_TYPE_INVALID: &str = "child_standard_handle_type_invalid";
const STANDARD_HANDLE_PIPE_QUERY_FAILED: &str = "child_standard_handle_pipe_query_failed";
const STANDARD_HANDLE_PIPE_END_INVALID: &str = "child_standard_handle_pipe_end_invalid";
const STANDARD_HANDLE_PIPE_MODE_INVALID: &str = "child_standard_handle_pipe_mode_invalid";
const STANDARD_HANDLE_FILE_MODE_QUERY_FAILED: &str = "child_standard_handle_file_mode_query_failed";
const STANDARD_HANDLE_FILE_MODE_INVALID: &str = "child_standard_handle_file_mode_invalid";
const STANDARD_HANDLE_ACCESS_UNAVAILABLE: &str = "child_standard_handle_access_unavailable";
const STANDARD_HANDLE_ACCESS_INVALID: &str = "child_standard_handle_access_invalid";
const STANDARD_HANDLE_SERVER_PID_INVALID: &str = "child_standard_handle_server_pid_invalid";
const STANDARD_HANDLE_SERVER_PID_MISMATCH: &str = "child_standard_handle_server_pid_mismatch";
const STANDARD_HANDLE_BINDING_INVALID: &str = "child_standard_handle_binding_invalid";
const STANDARD_HANDLE_OBJECT_ALIAS: &str = "child_standard_handle_object_alias";
const STANDARD_HANDLE_FLAGS_INVALID: &str = "child_standard_handle_flags_invalid";
const STANDARD_HANDLE_OWNERSHIP_UNAVAILABLE: &str = "child_standard_handle_ownership_unavailable";
const STANDARD_HANDLE_RUNTIME_REVALIDATION_FAILED: &str =
    "child_standard_handle_runtime_revalidation_failed";
const STANDARD_HANDLE_BOOTSTRAP_CLEAR_FAILED: &str = "child_standard_handle_bootstrap_clear_failed";

const SYNCHRONOUS_FILE_MODE_MASK: u32 = FILE_SYNCHRONOUS_IO_ALERT | FILE_SYNCHRONOUS_IO_NONALERT;
const KNOWN_NAMED_PIPE_INFO_FLAGS: u32 =
    PIPE_SERVER_END | PIPE_TYPE_MESSAGE | PIPE_REJECT_REMOTE_CLIENTS;

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ChildStandardHandleError(&'static str);

impl ChildStandardHandleError {
    fn new(code: &'static str) -> Self {
        Self(code)
    }

    pub(crate) fn code(&self) -> &'static str {
        self.0
    }
}

impl fmt::Display for ChildStandardHandleError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.0)
    }
}

impl std::error::Error for ChildStandardHandleError {}

/// Required parent/child sequencing for the future native launcher. No current
/// child path advances beyond `ChildSlotValidation`: the authenticated control
/// source needed to prove `ControlSlotReady` is intentionally absent.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub(crate) enum RequiredChildLaunchSequenceStep {
    ThreeServerEndsLive = 1,
    ChildSlotValidation = 2,
    ControlSlotReady = 3,
    ExactBootstrapFrameWritten = 4,
    BootstrapWriterOnlyClosed = 5,
    ChildBootstrapEofObserved = 6,
}

pub(crate) const REQUIRED_CHILD_LAUNCH_SEQUENCE: [RequiredChildLaunchSequenceStep; 6] = [
    RequiredChildLaunchSequenceStep::ThreeServerEndsLive,
    RequiredChildLaunchSequenceStep::ChildSlotValidation,
    RequiredChildLaunchSequenceStep::ControlSlotReady,
    RequiredChildLaunchSequenceStep::ExactBootstrapFrameWritten,
    RequiredChildLaunchSequenceStep::BootstrapWriterOnlyClosed,
    RequiredChildLaunchSequenceStep::ChildBootstrapEofObserved,
];

pub(crate) const REQUIRED_BOOTSTRAP_WRITE_LEN: usize = CHILD_BOOTSTRAP_FRAME_LEN;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct StartupHandleReadback {
    flags: u32,
    startup_handles: [usize; CHILD_STANDARD_HANDLE_SLOT_COUNT],
    standard_handles: [usize; CHILD_STANDARD_HANDLE_SLOT_COUNT],
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct ValidatedStandardHandle {
    purpose: ChildStandardHandlePurpose,
    raw_handle: usize,
    granted_access: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct NamedPipeModeReadback {
    read_mode: u32,
    completion_mode: u32,
}

/// A one-process witness that the three inherited standard handles match the
/// fixed production transport schema. It deliberately owns no expectation and
/// is not `Clone`; a later authenticated peer step must consume it.
pub(crate) struct ValidatedChildStandardHandleSet {
    role: ChildBootstrapRole,
    server_process_id: u32,
    handles: [ValidatedStandardHandle; CHILD_STANDARD_HANDLE_SLOT_COUNT],
    role_capability_set: RoleCapabilitySetBinding,
    may_take_inherited_handle_ownership: bool,
}

impl fmt::Debug for ValidatedChildStandardHandleSet {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ValidatedChildStandardHandleSet")
            .field("role", &self.role)
            .field("serverProcessId", &self.server_process_id)
            .field("handles", &"<redacted>")
            .field("roleCapabilitySet", &self.role_capability_set)
            .finish()
    }
}

impl ValidatedChildStandardHandleSet {
    pub(crate) fn validate_current_process(
        role: ChildBootstrapRole,
    ) -> Result<Self, ChildStandardHandleError> {
        let mut validated = validate_with_kernel(role, &mut WindowsChildTransportKernel)?;
        validated.may_take_inherited_handle_ownership = true;
        Ok(validated)
    }

    /// Affinely transfers the validated endpoints to the authenticated child
    /// handshake. The original witness exposes no reusable raw-handle getters.
    pub(crate) fn into_handshake_transport(
        self,
    ) -> Result<ChildHandshakeTransport, ChildStandardHandleError> {
        if !self.may_take_inherited_handle_ownership {
            return Err(ChildStandardHandleError::new(
                STANDARD_HANDLE_OWNERSHIP_UNAVAILABLE,
            ));
        }
        let raw_handles = self.handles.map(|handle| handle.raw_handle);
        let raw_handle_list_digest = RoleRawHandleListDigest::derive(self.role, &raw_handles)
            .map_err(|_| ChildStandardHandleError::new(STANDARD_HANDLE_BINDING_INVALID))?;
        if raw_handle_list_digest.as_bytes() != self.role_capability_set.raw_handle_list_digest() {
            return Err(ChildStandardHandleError::new(
                STANDARD_HANDLE_BINDING_INVALID,
            ));
        }
        let [bootstrap, control, result] = raw_handles;
        let endpoints = unsafe {
            ChildOwnedEndpointSet {
                bootstrap: OwnedHandle::from_raw_handle(raw_to_handle(bootstrap) as RawHandle),
                control: OwnedHandle::from_raw_handle(raw_to_handle(control) as RawHandle),
                result: OwnedHandle::from_raw_handle(raw_to_handle(result) as RawHandle),
            }
        };
        Ok(ChildHandshakeTransport {
            role: self.role,
            server_process_id: self.server_process_id,
            endpoints,
            role_capability_set: self.role_capability_set,
            raw_handle_list_digest,
        })
    }
}

/// Consumed, role-bound transport passed only to the authenticated handshake
/// adapter. It remains non-Clone and keeps the exact three endpoints together.
pub(crate) struct ChildHandshakeTransport {
    role: ChildBootstrapRole,
    server_process_id: u32,
    endpoints: ChildOwnedEndpointSet,
    role_capability_set: RoleCapabilitySetBinding,
    raw_handle_list_digest: RoleRawHandleListDigest,
}

struct ChildOwnedEndpointSet {
    bootstrap: OwnedHandle,
    control: OwnedHandle,
    result: OwnedHandle,
}

/// Authenticated runtime transport after the bootstrap read endpoint has been
/// consumed, removed from the process standard-handle table, and closed.
pub(crate) struct AuthenticatedChildRuntimeTransport {
    role: ChildBootstrapRole,
    server_process_id: u32,
    control: OwnedHandle,
    result: OwnedHandle,
    role_capability_set: RoleCapabilitySetBinding,
    raw_handle_list_digest: RoleRawHandleListDigest,
}

impl fmt::Debug for AuthenticatedChildRuntimeTransport {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("AuthenticatedChildRuntimeTransport")
            .field("role", &self.role)
            .field("serverProcessId", &self.server_process_id)
            .field("handles", &"<redacted>")
            .field("roleCapabilitySet", &self.role_capability_set)
            .field("rawHandleListDigest", &self.raw_handle_list_digest)
            .finish()
    }
}

impl fmt::Debug for ChildHandshakeTransport {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ChildHandshakeTransport")
            .field("role", &self.role)
            .field("serverProcessId", &self.server_process_id)
            .field("handles", &"<redacted>")
            .field("roleCapabilitySet", &self.role_capability_set)
            .finish()
    }
}

impl ChildHandshakeTransport {
    pub(crate) const fn role(&self) -> ChildBootstrapRole {
        self.role
    }

    pub(crate) const fn server_process_id(&self) -> u32 {
        self.server_process_id
    }

    pub(crate) fn role_capability_set(&self) -> &RoleCapabilitySetBinding {
        &self.role_capability_set
    }

    pub(crate) const fn raw_handle_list_digest(&self) -> RoleRawHandleListDigest {
        self.raw_handle_list_digest
    }

    pub(crate) fn bootstrap_read_handle(&self) -> HANDLE {
        self.endpoints.bootstrap.as_raw_handle().cast()
    }

    pub(crate) fn private_control_handle(&self) -> HANDLE {
        self.endpoints.control.as_raw_handle().cast()
    }

    pub(crate) fn structured_result_handle(&self) -> HANDLE {
        self.endpoints.result.as_raw_handle().cast()
    }

    fn raw_handles(&self) -> [usize; CHILD_STANDARD_HANDLE_SLOT_COUNT] {
        [
            self.bootstrap_read_handle() as usize,
            self.private_control_handle() as usize,
            self.structured_result_handle() as usize,
        ]
    }

    fn revalidate_for_runtime(&self) -> Result<(), ChildStandardHandleError> {
        let raw_handles = self.raw_handles();
        let standard_handles = unsafe {
            [
                handle_to_raw(GetStdHandle(STD_INPUT_HANDLE)),
                handle_to_raw(GetStdHandle(STD_OUTPUT_HANDLE)),
                handle_to_raw(GetStdHandle(STD_ERROR_HANDLE)),
            ]
        };
        if standard_handles != raw_handles {
            return Err(ChildStandardHandleError::new(
                STANDARD_HANDLE_RUNTIME_REVALIDATION_FAILED,
            ));
        }

        let mut kernel = WindowsChildTransportKernel;
        let current_process_id = kernel.current_process_id();
        if current_process_id == 0 || current_process_id == self.server_process_id {
            return Err(ChildStandardHandleError::new(
                STANDARD_HANDLE_RUNTIME_REVALIDATION_FAILED,
            ));
        }
        for (left_index, right_index) in [(0usize, 1usize), (0, 2), (1, 2)] {
            if kernel.same_object(raw_handles[left_index], raw_handles[right_index])? {
                return Err(ChildStandardHandleError::new(
                    STANDARD_HANDLE_RUNTIME_REVALIDATION_FAILED,
                ));
            }
        }

        let descriptors = child_role_capability_schema(self.role);
        let mut bindings = Vec::with_capacity(CHILD_STANDARD_HANDLE_SLOT_COUNT);
        for ((raw_handle, descriptor), index) in
            raw_handles.into_iter().zip(*descriptors).zip(0usize..)
        {
            if index >= CHILD_STANDARD_HANDLE_SLOT_COUNT
                || kernel.handle_flags(raw_handle)? != 0
                || kernel.file_type(raw_handle) != FILE_TYPE_PIPE
            {
                return Err(ChildStandardHandleError::new(
                    STANDARD_HANDLE_RUNTIME_REVALIDATION_FAILED,
                ));
            }
            let pipe_flags = kernel.named_pipe_flags(raw_handle)?;
            if pipe_flags & !KNOWN_NAMED_PIPE_INFO_FLAGS != 0
                || pipe_flags & (PIPE_SERVER_END | PIPE_TYPE_MESSAGE) != 0
                || kernel.file_mode(raw_handle)? != 0
            {
                return Err(ChildStandardHandleError::new(
                    STANDARD_HANDLE_RUNTIME_REVALIDATION_FAILED,
                ));
            }
            let pipe_mode = kernel.named_pipe_mode(raw_handle)?;
            if pipe_mode.read_mode != FILE_PIPE_BYTE_STREAM_MODE
                || pipe_mode.completion_mode != FILE_PIPE_QUEUE_OPERATION
            {
                return Err(ChildStandardHandleError::new(
                    STANDARD_HANDLE_RUNTIME_REVALIDATION_FAILED,
                ));
            }
            let purpose = descriptor.purpose();
            let granted_access = kernel.granted_access(raw_handle)?;
            if granted_access != expected_access(purpose)
                || kernel.server_process_id(raw_handle)? != self.server_process_id
            {
                return Err(ChildStandardHandleError::new(
                    STANDARD_HANDLE_RUNTIME_REVALIDATION_FAILED,
                ));
            }
            bindings.push(ChildRoleCapabilitySlotBinding::new(
                descriptor.semantic(),
                slot_capability_digest(
                    self.role,
                    descriptor.semantic(),
                    purpose,
                    granted_access,
                    self.server_process_id,
                ),
                raw_handle,
            ));
        }
        let bindings: [ChildRoleCapabilitySlotBinding; CHILD_STANDARD_HANDLE_SLOT_COUNT] =
            bindings.try_into().map_err(|_| {
                ChildStandardHandleError::new(STANDARD_HANDLE_RUNTIME_REVALIDATION_FAILED)
            })?;
        let capability_set = RoleCapabilitySetBinding::derive_from_fixed_slots(
            self.role, &bindings,
        )
        .map_err(|_| ChildStandardHandleError::new(STANDARD_HANDLE_RUNTIME_REVALIDATION_FAILED))?;
        let raw_handle_list_digest = RoleRawHandleListDigest::derive(self.role, &raw_handles)
            .map_err(|_| {
                ChildStandardHandleError::new(STANDARD_HANDLE_RUNTIME_REVALIDATION_FAILED)
            })?;
        if capability_set != self.role_capability_set
            || raw_handle_list_digest != self.raw_handle_list_digest
        {
            return Err(ChildStandardHandleError::new(
                STANDARD_HANDLE_RUNTIME_REVALIDATION_FAILED,
            ));
        }
        Ok(())
    }

    pub(crate) fn into_authenticated_runtime_transport(
        self,
    ) -> Result<AuthenticatedChildRuntimeTransport, ChildStandardHandleError> {
        self.revalidate_for_runtime()?;
        if unsafe { SetStdHandle(STD_INPUT_HANDLE, ptr::null_mut()) } == 0
            || !unsafe { GetStdHandle(STD_INPUT_HANDLE) }.is_null()
        {
            return Err(ChildStandardHandleError::new(
                STANDARD_HANDLE_BOOTSTRAP_CLEAR_FAILED,
            ));
        }
        let Self {
            role,
            server_process_id,
            endpoints,
            role_capability_set,
            raw_handle_list_digest,
        } = self;
        let ChildOwnedEndpointSet {
            bootstrap,
            control,
            result,
        } = endpoints;
        drop(bootstrap);
        Ok(AuthenticatedChildRuntimeTransport {
            role,
            server_process_id,
            control,
            result,
            role_capability_set,
            raw_handle_list_digest,
        })
    }
}

impl AuthenticatedChildRuntimeTransport {
    pub(crate) const fn role(&self) -> ChildBootstrapRole {
        self.role
    }

    pub(crate) fn private_control_handle(&self) -> HANDLE {
        self.control.as_raw_handle().cast()
    }

    pub(crate) fn structured_result_handle(&self) -> HANDLE {
        self.result.as_raw_handle().cast()
    }
}

trait ChildTransportKernel {
    fn startup_handle_readback(
        &mut self,
    ) -> Result<StartupHandleReadback, ChildStandardHandleError>;
    fn current_process_id(&mut self) -> u32;
    fn handle_flags(&mut self, raw_handle: usize) -> Result<u32, ChildStandardHandleError>;
    fn clear_inherit(&mut self, raw_handle: usize) -> Result<(), ChildStandardHandleError>;
    fn same_object(
        &mut self,
        left_raw_handle: usize,
        right_raw_handle: usize,
    ) -> Result<bool, ChildStandardHandleError>;
    fn file_type(&mut self, raw_handle: usize) -> u32;
    fn named_pipe_flags(&mut self, raw_handle: usize) -> Result<u32, ChildStandardHandleError>;
    fn file_mode(&mut self, raw_handle: usize) -> Result<u32, ChildStandardHandleError>;
    fn named_pipe_mode(
        &mut self,
        raw_handle: usize,
    ) -> Result<NamedPipeModeReadback, ChildStandardHandleError>;
    fn granted_access(&mut self, raw_handle: usize) -> Result<u32, ChildStandardHandleError>;
    fn server_process_id(&mut self, raw_handle: usize) -> Result<u32, ChildStandardHandleError>;
}

fn validate_with_kernel<K: ChildTransportKernel>(
    role: ChildBootstrapRole,
    kernel: &mut K,
) -> Result<ValidatedChildStandardHandleSet, ChildStandardHandleError> {
    let startup = kernel.startup_handle_readback()?;
    if startup.flags & STARTF_USESTDHANDLES == 0 {
        return Err(ChildStandardHandleError::new(STARTUP_CONTRACT_INVALID));
    }
    if startup.startup_handles != startup.standard_handles {
        return Err(ChildStandardHandleError::new(STANDARD_HANDLE_SLOT_MISMATCH));
    }

    let raw_handles = startup.standard_handles;
    if raw_handles
        .iter()
        .any(|raw| *raw == 0 || *raw == usize::MAX)
    {
        return Err(ChildStandardHandleError::new(STANDARD_HANDLE_INVALID));
    }
    for (index, raw) in raw_handles.iter().enumerate() {
        if raw_handles[..index].contains(raw) {
            return Err(ChildStandardHandleError::new(STANDARD_HANDLE_ALIAS));
        }
    }
    for (left_index, right_index) in [(0usize, 1usize), (0, 2), (1, 2)] {
        if kernel.same_object(raw_handles[left_index], raw_handles[right_index])? {
            return Err(ChildStandardHandleError::new(STANDARD_HANDLE_OBJECT_ALIAS));
        }
    }

    let mut inherit_missing = false;
    let mut flags_invalid = false;
    for raw in raw_handles {
        let flags = kernel.handle_flags(raw)?;
        if flags & HANDLE_FLAG_INHERIT == 0 {
            inherit_missing = true;
        } else if flags != HANDLE_FLAG_INHERIT {
            flags_invalid = true;
        }
    }

    let mut clear_failed = false;
    let mut clear_unverified = false;
    for raw in raw_handles {
        if kernel.clear_inherit(raw).is_err() {
            clear_failed = true;
            continue;
        }
        match kernel.handle_flags(raw) {
            Ok(0) => {}
            Ok(_) | Err(_) => clear_unverified = true,
        }
    }
    if inherit_missing {
        return Err(ChildStandardHandleError::new(
            STANDARD_HANDLE_INHERIT_MISSING,
        ));
    }
    if flags_invalid {
        return Err(ChildStandardHandleError::new(STANDARD_HANDLE_FLAGS_INVALID));
    }
    if clear_failed {
        return Err(ChildStandardHandleError::new(
            STANDARD_HANDLE_INHERIT_CLEAR_FAILED,
        ));
    }
    if clear_unverified {
        return Err(ChildStandardHandleError::new(
            STANDARD_HANDLE_INHERIT_CLEAR_UNVERIFIED,
        ));
    }

    let current_process_id = kernel.current_process_id();
    if current_process_id == 0 {
        return Err(ChildStandardHandleError::new(
            STANDARD_HANDLE_SERVER_PID_INVALID,
        ));
    }

    let descriptors = child_role_capability_schema(role);
    let mut validated = Vec::with_capacity(CHILD_STANDARD_HANDLE_SLOT_COUNT);
    let mut bindings = Vec::with_capacity(CHILD_STANDARD_HANDLE_SLOT_COUNT);
    let mut common_server_process_id = None;
    for ((raw_handle, descriptor), index) in raw_handles.into_iter().zip(*descriptors).zip(0usize..)
    {
        if index >= CHILD_STANDARD_HANDLE_SLOT_COUNT {
            return Err(ChildStandardHandleError::new(
                STANDARD_HANDLE_BINDING_INVALID,
            ));
        }
        if kernel.file_type(raw_handle) != FILE_TYPE_PIPE {
            return Err(ChildStandardHandleError::new(STANDARD_HANDLE_TYPE_INVALID));
        }
        let pipe_flags = kernel.named_pipe_flags(raw_handle)?;
        if pipe_flags & !KNOWN_NAMED_PIPE_INFO_FLAGS != 0 {
            return Err(ChildStandardHandleError::new(
                STANDARD_HANDLE_PIPE_MODE_INVALID,
            ));
        }
        if pipe_flags & PIPE_SERVER_END != 0 {
            return Err(ChildStandardHandleError::new(
                STANDARD_HANDLE_PIPE_END_INVALID,
            ));
        }
        if pipe_flags & PIPE_TYPE_MESSAGE != 0 {
            return Err(ChildStandardHandleError::new(
                STANDARD_HANDLE_PIPE_MODE_INVALID,
            ));
        }
        let file_mode = kernel.file_mode(raw_handle)?;
        // The fixed parent contract opens every client endpoint with no file
        // mode modifiers. In particular, either synchronous flag would make a
        // supposedly overlapped ReadFile/WriteFile block before a deadline can
        // be enforced; any other unmodelled bit also fails closed.
        if file_mode != 0 {
            return Err(ChildStandardHandleError::new(
                STANDARD_HANDLE_FILE_MODE_INVALID,
            ));
        }
        let pipe_mode = kernel.named_pipe_mode(raw_handle)?;
        if pipe_mode.read_mode != FILE_PIPE_BYTE_STREAM_MODE
            || pipe_mode.completion_mode != FILE_PIPE_QUEUE_OPERATION
        {
            return Err(ChildStandardHandleError::new(
                STANDARD_HANDLE_PIPE_MODE_INVALID,
            ));
        }
        let granted_access = kernel.granted_access(raw_handle)?;
        let purpose = descriptor.purpose();
        if granted_access != expected_access(purpose) {
            return Err(ChildStandardHandleError::new(
                STANDARD_HANDLE_ACCESS_INVALID,
            ));
        }
        let server_process_id = kernel.server_process_id(raw_handle)?;
        if server_process_id == 0 || server_process_id == current_process_id {
            return Err(ChildStandardHandleError::new(
                STANDARD_HANDLE_SERVER_PID_INVALID,
            ));
        }
        match common_server_process_id {
            Some(expected) if expected != server_process_id => {
                return Err(ChildStandardHandleError::new(
                    STANDARD_HANDLE_SERVER_PID_MISMATCH,
                ))
            }
            None => common_server_process_id = Some(server_process_id),
            Some(_) => {}
        }

        let capability_digest = slot_capability_digest(
            role,
            descriptor.semantic(),
            purpose,
            granted_access,
            server_process_id,
        );
        bindings.push(ChildRoleCapabilitySlotBinding::new(
            descriptor.semantic(),
            capability_digest,
            raw_handle,
        ));
        validated.push(ValidatedStandardHandle {
            purpose,
            raw_handle,
            granted_access,
        });
    }

    let handles: [ValidatedStandardHandle; CHILD_STANDARD_HANDLE_SLOT_COUNT] = validated
        .try_into()
        .map_err(|_| ChildStandardHandleError::new(STANDARD_HANDLE_BINDING_INVALID))?;
    let binding_array: [ChildRoleCapabilitySlotBinding; CHILD_STANDARD_HANDLE_SLOT_COUNT] =
        bindings
            .try_into()
            .map_err(|_| ChildStandardHandleError::new(STANDARD_HANDLE_BINDING_INVALID))?;
    let role_capability_set =
        RoleCapabilitySetBinding::derive_from_fixed_slots(role, binding_array.as_slice())
            .map_err(|_| ChildStandardHandleError::new(STANDARD_HANDLE_BINDING_INVALID))?;

    Ok(ValidatedChildStandardHandleSet {
        role,
        server_process_id: common_server_process_id
            .ok_or_else(|| ChildStandardHandleError::new(STANDARD_HANDLE_SERVER_PID_INVALID))?,
        handles,
        role_capability_set,
        may_take_inherited_handle_ownership: false,
    })
}

fn slot_capability_digest(
    role: ChildBootstrapRole,
    semantic: crate::primitive_evidence_child_protocol::ChildRoleCapabilitySlot,
    purpose: ChildStandardHandlePurpose,
    granted_access: u32,
    server_process_id: u32,
) -> BootstrapDigest {
    let mut digest = Sha256::new();
    digest.update(SLOT_CAPABILITY_DOMAIN);
    digest.update([role.wire_value()]);
    digest.update(semantic.wire_value().to_be_bytes());
    digest.update([purpose.wire_value()]);
    digest.update(FILE_TYPE_PIPE.to_be_bytes());
    digest.update(0u32.to_be_bytes());
    digest.update(granted_access.to_be_bytes());
    digest.update(server_process_id.to_be_bytes());
    digest.finalize().into()
}

// The service-side launcher consumes this projection. Both child test crates
// compile the shared source before that private adapter is linked.
#[cfg_attr(test, allow(dead_code))]
pub(crate) fn project_role_capability_set_from_verified_parent_pipe_contract(
    role: ChildBootstrapRole,
    raw_handles: [usize; CHILD_STANDARD_HANDLE_SLOT_COUNT],
    server_process_id: u32,
) -> Result<RoleCapabilitySetBinding, ChildStandardHandleError> {
    if server_process_id == 0
        || raw_handles
            .iter()
            .any(|handle| *handle == 0 || *handle == usize::MAX)
        || raw_handles
            .iter()
            .enumerate()
            .any(|(index, handle)| raw_handles[..index].contains(handle))
    {
        return Err(ChildStandardHandleError::new(
            STANDARD_HANDLE_BINDING_INVALID,
        ));
    }
    let descriptors = child_role_capability_schema(role);
    let bindings: [ChildRoleCapabilitySlotBinding; CHILD_STANDARD_HANDLE_SLOT_COUNT] =
        std::array::from_fn(|index| {
            let descriptor = descriptors[index];
            let purpose = descriptor.purpose();
            ChildRoleCapabilitySlotBinding::new(
                descriptor.semantic(),
                slot_capability_digest(
                    role,
                    descriptor.semantic(),
                    purpose,
                    expected_access(purpose),
                    server_process_id,
                ),
                raw_handles[index],
            )
        });
    RoleCapabilitySetBinding::derive_from_fixed_slots(role, &bindings)
        .map_err(|_| ChildStandardHandleError::new(STANDARD_HANDLE_BINDING_INVALID))
}

const fn expected_access(purpose: ChildStandardHandlePurpose) -> u32 {
    let contract = purpose.access_contract();
    let mut access = 0;
    if contract.readable() {
        access |= FILE_GENERIC_READ;
    }
    if contract.writable() {
        access |= FILE_GENERIC_WRITE;
    }
    if contract.metadata_readable() {
        access |= FILE_READ_ATTRIBUTES;
    }
    access
}

struct WindowsChildTransportKernel;

impl ChildTransportKernel for WindowsChildTransportKernel {
    fn startup_handle_readback(
        &mut self,
    ) -> Result<StartupHandleReadback, ChildStandardHandleError> {
        let mut startup = unsafe { std::mem::zeroed::<STARTUPINFOW>() };
        startup.cb = size_of::<STARTUPINFOW>() as u32;
        unsafe { GetStartupInfoW(&mut startup) };
        let startup_handles = [
            handle_to_raw(startup.hStdInput),
            handle_to_raw(startup.hStdOutput),
            handle_to_raw(startup.hStdError),
        ];
        let standard_handles = unsafe {
            [
                handle_to_raw(GetStdHandle(STD_INPUT_HANDLE)),
                handle_to_raw(GetStdHandle(STD_OUTPUT_HANDLE)),
                handle_to_raw(GetStdHandle(STD_ERROR_HANDLE)),
            ]
        };
        Ok(StartupHandleReadback {
            flags: startup.dwFlags,
            startup_handles,
            standard_handles,
        })
    }

    fn current_process_id(&mut self) -> u32 {
        unsafe { GetCurrentProcessId() }
    }

    fn handle_flags(&mut self, raw_handle: usize) -> Result<u32, ChildStandardHandleError> {
        let mut flags = 0u32;
        if unsafe { GetHandleInformation(raw_to_handle(raw_handle), &mut flags) } == 0 {
            return Err(ChildStandardHandleError::new(STANDARD_HANDLE_INVALID));
        }
        Ok(flags)
    }

    fn clear_inherit(&mut self, raw_handle: usize) -> Result<(), ChildStandardHandleError> {
        if unsafe { SetHandleInformation(raw_to_handle(raw_handle), HANDLE_FLAG_INHERIT, 0) } == 0 {
            return Err(ChildStandardHandleError::new(
                STANDARD_HANDLE_INHERIT_CLEAR_FAILED,
            ));
        }
        Ok(())
    }

    fn same_object(
        &mut self,
        left_raw_handle: usize,
        right_raw_handle: usize,
    ) -> Result<bool, ChildStandardHandleError> {
        Ok(unsafe {
            CompareObjectHandles(
                raw_to_handle(left_raw_handle),
                raw_to_handle(right_raw_handle),
            )
        } != 0)
    }

    fn file_type(&mut self, raw_handle: usize) -> u32 {
        unsafe { GetFileType(raw_to_handle(raw_handle)) }
    }

    fn named_pipe_flags(&mut self, raw_handle: usize) -> Result<u32, ChildStandardHandleError> {
        let mut flags = 0u32;
        if unsafe {
            GetNamedPipeInfo(
                raw_to_handle(raw_handle),
                &mut flags,
                ptr::null_mut(),
                ptr::null_mut(),
                ptr::null_mut(),
            )
        } == 0
        {
            return Err(ChildStandardHandleError::new(
                STANDARD_HANDLE_PIPE_QUERY_FAILED,
            ));
        }
        Ok(flags)
    }

    fn file_mode(&mut self, raw_handle: usize) -> Result<u32, ChildStandardHandleError> {
        let mut io_status = unsafe { std::mem::zeroed::<IO_STATUS_BLOCK>() };
        let mut information = unsafe { std::mem::zeroed::<FILE_MODE_INFORMATION>() };
        let status = unsafe {
            NtQueryInformationFile(
                raw_to_handle(raw_handle),
                &mut io_status,
                (&mut information as *mut FILE_MODE_INFORMATION).cast(),
                size_of::<FILE_MODE_INFORMATION>() as u32,
                FileModeInformation,
            )
        };
        if status != 0 || io_status.Information != size_of::<FILE_MODE_INFORMATION>() {
            return Err(ChildStandardHandleError::new(
                STANDARD_HANDLE_FILE_MODE_QUERY_FAILED,
            ));
        }
        Ok(information.Mode)
    }

    fn named_pipe_mode(
        &mut self,
        raw_handle: usize,
    ) -> Result<NamedPipeModeReadback, ChildStandardHandleError> {
        let mut io_status = unsafe { std::mem::zeroed::<IO_STATUS_BLOCK>() };
        let mut information = unsafe { std::mem::zeroed::<FILE_PIPE_INFORMATION>() };
        let status = unsafe {
            NtQueryInformationFile(
                raw_to_handle(raw_handle),
                &mut io_status,
                (&mut information as *mut FILE_PIPE_INFORMATION).cast(),
                size_of::<FILE_PIPE_INFORMATION>() as u32,
                FilePipeInformation,
            )
        };
        if status != 0 || io_status.Information != size_of::<FILE_PIPE_INFORMATION>() {
            return Err(ChildStandardHandleError::new(
                STANDARD_HANDLE_PIPE_QUERY_FAILED,
            ));
        }
        Ok(NamedPipeModeReadback {
            read_mode: information.ReadMode,
            completion_mode: information.CompletionMode,
        })
    }

    fn granted_access(&mut self, raw_handle: usize) -> Result<u32, ChildStandardHandleError> {
        let mut information = unsafe { std::mem::zeroed::<PublicObjectBasicInformation>() };
        let mut returned = 0u32;
        let status = unsafe {
            NtQueryObject(
                raw_to_handle(raw_handle),
                ObjectBasicInformation,
                (&mut information as *mut PublicObjectBasicInformation).cast(),
                size_of::<PublicObjectBasicInformation>() as u32,
                &mut returned,
            )
        };
        if status < 0 || returned < (size_of::<u32>() * 2) as u32 {
            return Err(ChildStandardHandleError::new(
                STANDARD_HANDLE_ACCESS_UNAVAILABLE,
            ));
        }
        Ok(information.granted_access)
    }

    fn server_process_id(&mut self, raw_handle: usize) -> Result<u32, ChildStandardHandleError> {
        let mut process_id = 0u32;
        if unsafe { GetNamedPipeServerProcessId(raw_to_handle(raw_handle), &mut process_id) } == 0 {
            return Err(ChildStandardHandleError::new(
                STANDARD_HANDLE_PIPE_QUERY_FAILED,
            ));
        }
        Ok(process_id)
    }
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

fn handle_to_raw(handle: HANDLE) -> usize {
    handle as usize
}

fn raw_to_handle(raw_handle: usize) -> HANDLE {
    raw_handle as HANDLE
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        collections::BTreeMap,
        os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle, RawHandle},
        sync::atomic::{AtomicU64, Ordering},
    };
    use windows_sys::Win32::{
        Foundation::{
            GENERIC_READ, GENERIC_WRITE, HANDLE_FLAG_PROTECT_FROM_CLOSE, INVALID_HANDLE_VALUE,
        },
        Storage::FileSystem::{
            CreateFileW, FILE_FLAG_FIRST_PIPE_INSTANCE, FILE_FLAG_OVERLAPPED, OPEN_EXISTING,
            PIPE_ACCESS_DUPLEX, PIPE_ACCESS_INBOUND, PIPE_ACCESS_OUTBOUND,
        },
        System::Pipes::{
            CreateNamedPipeW, GetNamedPipeHandleStateW, PIPE_NOWAIT, PIPE_READMODE_BYTE,
            PIPE_READMODE_MESSAGE, PIPE_TYPE_BYTE, PIPE_WAIT,
        },
    };

    const HANDLES: [usize; CHILD_STANDARD_HANDLE_SLOT_COUNT] = [0x101, 0x202, 0x303];
    const SERVICE_PROCESS_ID: u32 = 4_242;
    const CHILD_PROCESS_ID: u32 = 4_243;
    static REAL_PIPE_SEQUENCE: AtomicU64 = AtomicU64::new(1);

    struct RealPipeEndpoint {
        _server: OwnedHandle,
        client: OwnedHandle,
    }

    fn real_pipe_endpoint(
        server_access: u32,
        client_access: u32,
        overlapped: bool,
    ) -> RealPipeEndpoint {
        let sequence = REAL_PIPE_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        let name = format!(
            r"\\.\pipe\VRCForge.ChildTransport.ModeTest.{}.{}",
            unsafe { GetCurrentProcessId() },
            sequence,
        )
        .encode_utf16()
        .chain(std::iter::once(0))
        .collect::<Vec<_>>();
        let overlapped_flag = if overlapped { FILE_FLAG_OVERLAPPED } else { 0 };
        let raw_server = unsafe {
            CreateNamedPipeW(
                name.as_ptr(),
                server_access | FILE_FLAG_FIRST_PIPE_INSTANCE | overlapped_flag,
                PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
                1,
                4096,
                4096,
                5_000,
                ptr::null(),
            )
        };
        assert_ne!(raw_server, INVALID_HANDLE_VALUE);
        let server = unsafe { OwnedHandle::from_raw_handle(raw_server as RawHandle) };
        let raw_client = unsafe {
            CreateFileW(
                name.as_ptr(),
                client_access,
                0,
                ptr::null(),
                OPEN_EXISTING,
                overlapped_flag,
                ptr::null_mut(),
            )
        };
        assert_ne!(raw_client, INVALID_HANDLE_VALUE);
        RealPipeEndpoint {
            _server: server,
            client: unsafe { OwnedHandle::from_raw_handle(raw_client as RawHandle) },
        }
    }

    struct FakeKernel {
        startup: StartupHandleReadback,
        current_process_id: u32,
        flags: BTreeMap<usize, u32>,
        object_identities: BTreeMap<usize, u64>,
        file_types: BTreeMap<usize, u32>,
        pipe_flags: BTreeMap<usize, u32>,
        file_modes: BTreeMap<usize, u32>,
        pipe_modes: BTreeMap<usize, NamedPipeModeReadback>,
        accesses: BTreeMap<usize, u32>,
        server_process_ids: BTreeMap<usize, u32>,
        fail_clear: Option<usize>,
        sticky_inherit: Option<usize>,
        fail_file_mode_query: Option<usize>,
        fail_pipe_mode_query: Option<usize>,
    }

    impl FakeKernel {
        fn valid() -> Self {
            let purposes = child_role_capability_schema(ChildBootstrapRole::LifecycleDriver)
                .map(|descriptor| descriptor.purpose());
            Self {
                startup: StartupHandleReadback {
                    flags: STARTF_USESTDHANDLES,
                    startup_handles: HANDLES,
                    standard_handles: HANDLES,
                },
                current_process_id: CHILD_PROCESS_ID,
                flags: HANDLES
                    .into_iter()
                    .map(|handle| (handle, HANDLE_FLAG_INHERIT))
                    .collect(),
                object_identities: HANDLES
                    .into_iter()
                    .enumerate()
                    .map(|(index, handle)| (handle, 0xA000 + index as u64))
                    .collect(),
                file_types: HANDLES
                    .into_iter()
                    .map(|handle| (handle, FILE_TYPE_PIPE))
                    .collect(),
                pipe_flags: HANDLES
                    .into_iter()
                    .map(|handle| (handle, PIPE_REJECT_REMOTE_CLIENTS))
                    .collect(),
                file_modes: HANDLES.into_iter().map(|handle| (handle, 0)).collect(),
                pipe_modes: HANDLES
                    .into_iter()
                    .map(|handle| {
                        (
                            handle,
                            NamedPipeModeReadback {
                                read_mode: FILE_PIPE_BYTE_STREAM_MODE,
                                completion_mode: FILE_PIPE_QUEUE_OPERATION,
                            },
                        )
                    })
                    .collect(),
                accesses: HANDLES
                    .into_iter()
                    .zip(purposes)
                    .map(|(handle, purpose)| (handle, expected_access(purpose)))
                    .collect(),
                server_process_ids: HANDLES
                    .into_iter()
                    .map(|handle| (handle, SERVICE_PROCESS_ID))
                    .collect(),
                fail_clear: None,
                sticky_inherit: None,
                fail_file_mode_query: None,
                fail_pipe_mode_query: None,
            }
        }

        fn value(
            map: &BTreeMap<usize, u32>,
            raw_handle: usize,
        ) -> Result<u32, ChildStandardHandleError> {
            map.get(&raw_handle)
                .copied()
                .ok_or_else(|| ChildStandardHandleError::new(STANDARD_HANDLE_INVALID))
        }

        fn value_u64(
            map: &BTreeMap<usize, u64>,
            raw_handle: usize,
        ) -> Result<u64, ChildStandardHandleError> {
            map.get(&raw_handle)
                .copied()
                .ok_or_else(|| ChildStandardHandleError::new(STANDARD_HANDLE_INVALID))
        }
    }

    impl ChildTransportKernel for FakeKernel {
        fn startup_handle_readback(
            &mut self,
        ) -> Result<StartupHandleReadback, ChildStandardHandleError> {
            Ok(self.startup)
        }

        fn current_process_id(&mut self) -> u32 {
            self.current_process_id
        }

        fn handle_flags(&mut self, raw_handle: usize) -> Result<u32, ChildStandardHandleError> {
            Self::value(&self.flags, raw_handle)
        }

        fn clear_inherit(&mut self, raw_handle: usize) -> Result<(), ChildStandardHandleError> {
            if self.fail_clear == Some(raw_handle) {
                return Err(ChildStandardHandleError::new(
                    STANDARD_HANDLE_INHERIT_CLEAR_FAILED,
                ));
            }
            if self.sticky_inherit != Some(raw_handle) {
                let flags = self
                    .flags
                    .get_mut(&raw_handle)
                    .ok_or_else(|| ChildStandardHandleError::new(STANDARD_HANDLE_INVALID))?;
                *flags &= !HANDLE_FLAG_INHERIT;
            }
            Ok(())
        }

        fn same_object(
            &mut self,
            left_raw_handle: usize,
            right_raw_handle: usize,
        ) -> Result<bool, ChildStandardHandleError> {
            Ok(Self::value_u64(&self.object_identities, left_raw_handle)?
                == Self::value_u64(&self.object_identities, right_raw_handle)?)
        }

        fn file_type(&mut self, raw_handle: usize) -> u32 {
            Self::value(&self.file_types, raw_handle).unwrap_or_default()
        }

        fn named_pipe_flags(&mut self, raw_handle: usize) -> Result<u32, ChildStandardHandleError> {
            Self::value(&self.pipe_flags, raw_handle)
        }

        fn file_mode(&mut self, raw_handle: usize) -> Result<u32, ChildStandardHandleError> {
            if self.fail_file_mode_query == Some(raw_handle) {
                return Err(ChildStandardHandleError::new(
                    STANDARD_HANDLE_FILE_MODE_QUERY_FAILED,
                ));
            }
            Self::value(&self.file_modes, raw_handle)
        }

        fn named_pipe_mode(
            &mut self,
            raw_handle: usize,
        ) -> Result<NamedPipeModeReadback, ChildStandardHandleError> {
            if self.fail_pipe_mode_query == Some(raw_handle) {
                return Err(ChildStandardHandleError::new(
                    STANDARD_HANDLE_PIPE_QUERY_FAILED,
                ));
            }
            self.pipe_modes
                .get(&raw_handle)
                .copied()
                .ok_or_else(|| ChildStandardHandleError::new(STANDARD_HANDLE_INVALID))
        }

        fn granted_access(&mut self, raw_handle: usize) -> Result<u32, ChildStandardHandleError> {
            Self::value(&self.accesses, raw_handle)
        }

        fn server_process_id(
            &mut self,
            raw_handle: usize,
        ) -> Result<u32, ChildStandardHandleError> {
            Self::value(&self.server_process_ids, raw_handle)
        }
    }

    fn error_code(kernel: &mut FakeKernel) -> &'static str {
        validate_with_kernel(ChildBootstrapRole::LifecycleDriver, kernel)
            .expect_err("hostile transport must fail")
            .code()
    }

    #[test]
    fn exact_three_slot_contract_yields_one_non_clone_witness_and_clears_inherit() {
        let mut kernel = FakeKernel::valid();
        let validated = validate_with_kernel(ChildBootstrapRole::LifecycleDriver, &mut kernel)
            .expect("fixed child standard handles");
        assert_eq!(validated.role, ChildBootstrapRole::LifecycleDriver);
        assert_eq!(validated.server_process_id, SERVICE_PROCESS_ID);
        assert_eq!(validated.role_capability_set.slot_count(), 3);
        assert_eq!(
            RoleRawHandleListDigest::derive(ChildBootstrapRole::LifecycleDriver, &HANDLES)
                .unwrap()
                .as_bytes(),
            validated.role_capability_set.raw_handle_list_digest()
        );
        assert_eq!(
            validated
                .into_handshake_transport()
                .expect_err("a synthetic kernel cannot authorize handle ownership")
                .code(),
            STANDARD_HANDLE_OWNERSHIP_UNAVAILABLE
        );
        assert!(kernel
            .flags
            .values()
            .all(|flags| flags & HANDLE_FLAG_INHERIT == 0));
    }

    #[test]
    fn real_named_pipe_clients_expose_async_byte_wait_mode_and_exact_access() {
        for (server_access, client_access) in [
            (PIPE_ACCESS_OUTBOUND, GENERIC_READ),
            (PIPE_ACCESS_DUPLEX, GENERIC_READ | GENERIC_WRITE),
            (PIPE_ACCESS_INBOUND, GENERIC_WRITE | FILE_READ_ATTRIBUTES),
        ] {
            let endpoint = real_pipe_endpoint(server_access, client_access, true);
            let raw_handle = endpoint.client.as_raw_handle() as usize;
            let mut kernel = WindowsChildTransportKernel;
            assert_eq!(kernel.file_mode(raw_handle).unwrap(), 0);
            assert_eq!(
                kernel.named_pipe_mode(raw_handle).unwrap(),
                NamedPipeModeReadback {
                    read_mode: FILE_PIPE_BYTE_STREAM_MODE,
                    completion_mode: FILE_PIPE_QUEUE_OPERATION,
                }
            );
            let mut state = u32::MAX;
            assert_ne!(
                unsafe {
                    GetNamedPipeHandleStateW(
                        raw_to_handle(raw_handle),
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
            assert_eq!(state & (PIPE_NOWAIT | PIPE_READMODE_MESSAGE), 0);
            let purpose = match server_access {
                PIPE_ACCESS_OUTBOUND => ChildStandardHandlePurpose::BootstrapRead,
                PIPE_ACCESS_DUPLEX => ChildStandardHandlePurpose::PrivateControlDuplex,
                PIPE_ACCESS_INBOUND => ChildStandardHandlePurpose::StructuredResultWrite,
                _ => unreachable!("fixed real pipe direction"),
            };
            assert_eq!(
                kernel.granted_access(raw_handle).unwrap(),
                expected_access(purpose)
            );
        }

        let synchronous = real_pipe_endpoint(PIPE_ACCESS_OUTBOUND, GENERIC_READ, false);
        let mut kernel = WindowsChildTransportKernel;
        let mode = kernel
            .file_mode(synchronous.client.as_raw_handle() as usize)
            .unwrap();
        assert_ne!(mode & SYNCHRONOUS_FILE_MODE_MASK, 0);
    }

    #[test]
    fn future_parent_sequence_keeps_servers_live_until_child_observes_writer_only_eof() {
        assert_eq!(REQUIRED_BOOTSTRAP_WRITE_LEN, 336);
        assert_eq!(
            REQUIRED_CHILD_LAUNCH_SEQUENCE,
            [
                RequiredChildLaunchSequenceStep::ThreeServerEndsLive,
                RequiredChildLaunchSequenceStep::ChildSlotValidation,
                RequiredChildLaunchSequenceStep::ControlSlotReady,
                RequiredChildLaunchSequenceStep::ExactBootstrapFrameWritten,
                RequiredChildLaunchSequenceStep::BootstrapWriterOnlyClosed,
                RequiredChildLaunchSequenceStep::ChildBootstrapEofObserved,
            ]
        );
    }

    #[test]
    fn startup_flag_and_wrong_standard_slot_are_rejected() {
        let mut missing_flag = FakeKernel::valid();
        missing_flag.startup.flags = 0;
        assert_eq!(error_code(&mut missing_flag), STARTUP_CONTRACT_INVALID);

        let mut wrong_slot = FakeKernel::valid();
        wrong_slot.startup.standard_handles.swap(0, 1);
        assert_eq!(error_code(&mut wrong_slot), STANDARD_HANDLE_SLOT_MISMATCH);
    }

    #[test]
    fn invalid_and_aliased_handles_are_rejected_before_kernel_probing() {
        let mut invalid = FakeKernel::valid();
        invalid.startup.startup_handles[0] = 0;
        invalid.startup.standard_handles[0] = 0;
        assert_eq!(error_code(&mut invalid), STANDARD_HANDLE_INVALID);

        let mut alias = FakeKernel::valid();
        alias.startup.startup_handles[1] = HANDLES[0];
        alias.startup.standard_handles[1] = HANDLES[0];
        assert_eq!(error_code(&mut alias), STANDARD_HANDLE_ALIAS);

        for (left_index, right_index) in [(0usize, 1usize), (0, 2), (1, 2)] {
            let mut duplicated_object = FakeKernel::valid();
            let left_identity = duplicated_object.object_identities[&HANDLES[left_index]];
            duplicated_object
                .object_identities
                .insert(HANDLES[right_index], left_identity);
            assert_ne!(HANDLES[left_index], HANDLES[right_index]);
            assert_eq!(
                error_code(&mut duplicated_object),
                STANDARD_HANDLE_OBJECT_ALIAS
            );
        }
    }

    #[test]
    fn disk_and_server_pipe_end_are_rejected() {
        let mut disk = FakeKernel::valid();
        disk.file_types.insert(HANDLES[0], 1);
        assert_eq!(error_code(&mut disk), STANDARD_HANDLE_TYPE_INVALID);

        let mut server_end = FakeKernel::valid();
        server_end.pipe_flags.insert(HANDLES[1], PIPE_SERVER_END);
        assert_eq!(
            error_code(&mut server_end),
            STANDARD_HANDLE_PIPE_END_INVALID
        );
    }

    #[test]
    fn synchronous_or_unknown_file_object_modes_are_rejected() {
        for mode in [
            FILE_SYNCHRONOUS_IO_ALERT,
            FILE_SYNCHRONOUS_IO_NONALERT,
            FILE_SYNCHRONOUS_IO_ALERT | FILE_SYNCHRONOUS_IO_NONALERT,
            0x8000_0000,
        ] {
            let mut kernel = FakeKernel::valid();
            kernel.file_modes.insert(HANDLES[1], mode);
            assert_eq!(error_code(&mut kernel), STANDARD_HANDLE_FILE_MODE_INVALID);
        }

        let mut query_failed = FakeKernel::valid();
        query_failed.fail_file_mode_query = Some(HANDLES[0]);
        assert_eq!(
            error_code(&mut query_failed),
            STANDARD_HANDLE_FILE_MODE_QUERY_FAILED
        );
    }

    #[test]
    fn message_nonblocking_and_unknown_pipe_modes_are_rejected() {
        let mut message_type = FakeKernel::valid();
        message_type
            .pipe_flags
            .insert(HANDLES[0], PIPE_REJECT_REMOTE_CLIENTS | PIPE_TYPE_MESSAGE);
        assert_eq!(
            error_code(&mut message_type),
            STANDARD_HANDLE_PIPE_MODE_INVALID
        );

        let mut message_read = FakeKernel::valid();
        message_read
            .pipe_modes
            .get_mut(&HANDLES[1])
            .unwrap()
            .read_mode = 1;
        assert_eq!(
            error_code(&mut message_read),
            STANDARD_HANDLE_PIPE_MODE_INVALID
        );

        let mut nonblocking = FakeKernel::valid();
        nonblocking
            .pipe_modes
            .get_mut(&HANDLES[2])
            .unwrap()
            .completion_mode = 1;
        assert_eq!(
            error_code(&mut nonblocking),
            STANDARD_HANDLE_PIPE_MODE_INVALID
        );

        for (read_mode, completion_mode) in [(2, 0), (0, 2), (u32::MAX, u32::MAX)] {
            let mut unknown = FakeKernel::valid();
            unknown.pipe_modes.insert(
                HANDLES[0],
                NamedPipeModeReadback {
                    read_mode,
                    completion_mode,
                },
            );
            assert_eq!(error_code(&mut unknown), STANDARD_HANDLE_PIPE_MODE_INVALID);
        }

        let mut unknown_info_flag = FakeKernel::valid();
        unknown_info_flag
            .pipe_flags
            .insert(HANDLES[0], PIPE_REJECT_REMOTE_CLIENTS | 0x8000_0000);
        assert_eq!(
            error_code(&mut unknown_info_flag),
            STANDARD_HANDLE_PIPE_MODE_INVALID
        );

        let mut query_failed = FakeKernel::valid();
        query_failed.fail_pipe_mode_query = Some(HANDLES[2]);
        assert_eq!(
            error_code(&mut query_failed),
            STANDARD_HANDLE_PIPE_QUERY_FAILED
        );
    }

    #[test]
    fn every_slot_requires_its_exact_access_direction() {
        let purposes = child_role_capability_schema(ChildBootstrapRole::LifecycleDriver)
            .map(|descriptor| descriptor.purpose());
        for (index, purpose) in purposes.into_iter().enumerate() {
            let mut kernel = FakeKernel::valid();
            kernel
                .accesses
                .insert(HANDLES[index], expected_access(purpose) ^ FILE_GENERIC_READ);
            assert_eq!(error_code(&mut kernel), STANDARD_HANDLE_ACCESS_INVALID);
        }

        let mut result_missing_metadata = FakeKernel::valid();
        result_missing_metadata
            .accesses
            .insert(HANDLES[2], FILE_GENERIC_WRITE);
        assert_eq!(
            error_code(&mut result_missing_metadata),
            STANDARD_HANDLE_ACCESS_INVALID
        );

        let mut result_with_extra_data_read = FakeKernel::valid();
        let result_access = result_with_extra_data_read.accesses[&HANDLES[2]];
        result_with_extra_data_read
            .accesses
            .insert(HANDLES[2], result_access | FILE_GENERIC_READ);
        assert_eq!(
            error_code(&mut result_with_extra_data_read),
            STANDARD_HANDLE_ACCESS_INVALID
        );
    }

    #[test]
    fn zero_self_and_mixed_server_process_ids_are_rejected() {
        for invalid_pid in [0, CHILD_PROCESS_ID] {
            let mut kernel = FakeKernel::valid();
            kernel.server_process_ids.insert(HANDLES[0], invalid_pid);
            assert_eq!(error_code(&mut kernel), STANDARD_HANDLE_SERVER_PID_INVALID);
        }

        let mut mixed = FakeKernel::valid();
        mixed
            .server_process_ids
            .insert(HANDLES[2], SERVICE_PROCESS_ID + 2);
        assert_eq!(error_code(&mut mixed), STANDARD_HANDLE_SERVER_PID_MISMATCH);
    }

    #[test]
    fn inherited_flag_must_exist_then_clear_and_read_back() {
        let mut missing = FakeKernel::valid();
        missing.flags.insert(HANDLES[0], 0);
        assert_eq!(error_code(&mut missing), STANDARD_HANDLE_INHERIT_MISSING);
        assert!(missing
            .flags
            .values()
            .all(|flags| flags & HANDLE_FLAG_INHERIT == 0));

        let mut clear_failed = FakeKernel::valid();
        clear_failed.fail_clear = Some(HANDLES[1]);
        assert_eq!(
            error_code(&mut clear_failed),
            STANDARD_HANDLE_INHERIT_CLEAR_FAILED
        );

        let mut sticky = FakeKernel::valid();
        sticky.sticky_inherit = Some(HANDLES[2]);
        assert_eq!(
            error_code(&mut sticky),
            STANDARD_HANDLE_INHERIT_CLEAR_UNVERIFIED
        );

        let mut protected = FakeKernel::valid();
        protected.flags.insert(
            HANDLES[1],
            HANDLE_FLAG_INHERIT | HANDLE_FLAG_PROTECT_FROM_CLOSE,
        );
        assert_eq!(error_code(&mut protected), STANDARD_HANDLE_FLAGS_INVALID);
    }

    #[test]
    fn role_specific_semantics_change_the_joint_binding() {
        let mut driver_kernel = FakeKernel::valid();
        let driver =
            validate_with_kernel(ChildBootstrapRole::LifecycleDriver, &mut driver_kernel).unwrap();
        let mut bridge_kernel = FakeKernel::valid();
        let bridge =
            validate_with_kernel(ChildBootstrapRole::BridgeLauncher, &mut bridge_kernel).unwrap();
        assert_ne!(
            driver.role_capability_set.semantic_digest(),
            bridge.role_capability_set.semantic_digest()
        );
        assert_ne!(
            driver.role_capability_set.raw_handle_list_digest(),
            bridge.role_capability_set.raw_handle_list_digest()
        );
    }
}

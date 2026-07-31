//! Held-handle security sealing for the elevated authority finalizer.
//!
//! The caller must already have authenticated the object and opened the
//! sealing handle with only the rights required by this transition. This
//! module never resolves a path while that writable handle is live. It checks
//! the complete staging descriptor, uses the file-object security API to seal
//! the DACL and mandatory label on the same kernel object, closes the writable
//! handle, and only then invokes the caller's read-only reopen operation for
//! final identity and content verification.

#![cfg(windows)]

use super::security_policy::{
    BINARY_SEALED_SDDL, BINARY_STAGING_SDDL, CANDIDATE_CONSUMPTION_NAMESPACE_SDDL,
    CANDIDATE_CONSUMPTION_STAGING_SDDL, FINALIZER_COMMIT_RECEIPT_IMMUTABLE_SDDL,
    FINALIZER_COMMIT_TRANSACTION_PROGRESS_HANDLE_ACCESS, FINALIZER_COMMIT_TRANSACTION_ROOT_SDDL,
    FINALIZER_PUBLISHING_PRIVATE_SEALED_SDDL, GENERATION_SEALED_SDDL, GENERATION_STAGING_SDDL,
    LEDGER_FINAL_SDDL, LEDGER_STAGING_SDDL, NONCE_SEALED_SDDL, RUNTIME_BLOB_DIRECTORY_FINAL_SDDL,
    RUNTIME_BLOB_DIRECTORY_STAGING_SDDL, RUNTIME_BLOB_FILE_SDDL, STABLE_ROOT_SDDL,
    STATE_IMMUTABLE_SDDL, STATE_STAGING_SDDL, WORKER_NONCE_NAMESPACE_SDDL,
    WORKER_NONCE_STAGING_SDDL,
};
use super::{MAX_AUTHORITY_BINARY_BYTES, MAX_RUNTIME_SOURCE_MANIFEST_BYTES};
use sha2::{Digest, Sha256};
use std::{
    fmt,
    mem::{size_of, zeroed},
    os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle, RawHandle},
    ptr,
};
use windows_sys::{
    Wdk::Foundation::{NtQueryObject, ObjectBasicInformation},
    Win32::{
        Foundation::{
            GetLastError, LocalFree, SetLastError, ERROR_NOT_ALL_ASSIGNED, HANDLE,
            INVALID_HANDLE_VALUE, LUID,
        },
        Security::{
            AdjustTokenPrivileges,
            Authorization::{
                ConvertSecurityDescriptorToStringSecurityDescriptorW,
                ConvertStringSecurityDescriptorToSecurityDescriptorW, GetSecurityInfo,
                SetSecurityInfo, SDDL_REVISION_1, SE_FILE_OBJECT,
            },
            GetSecurityDescriptorDacl, GetSecurityDescriptorSacl, LookupPrivilegeValueW, ACL,
            DACL_SECURITY_INFORMATION, GROUP_SECURITY_INFORMATION, LABEL_SECURITY_INFORMATION,
            LUID_AND_ATTRIBUTES, OWNER_SECURITY_INFORMATION, PROTECTED_DACL_SECURITY_INFORMATION,
            PSECURITY_DESCRIPTOR, SE_PRIVILEGE_ENABLED, SE_SECURITY_NAME, TOKEN_ADJUST_PRIVILEGES,
            TOKEN_PRIVILEGES, TOKEN_QUERY,
        },
        Storage::FileSystem::{
            GetFileInformationByHandle, ReadFile, SetFilePointerEx, BY_HANDLE_FILE_INFORMATION,
            DELETE, FILE_APPEND_DATA, FILE_ATTRIBUTE_DIRECTORY, FILE_ATTRIBUTE_REPARSE_POINT,
            FILE_BEGIN, FILE_DELETE_CHILD, FILE_EXECUTE, FILE_READ_ATTRIBUTES, FILE_READ_DATA,
            FILE_READ_EA, FILE_WRITE_ATTRIBUTES, FILE_WRITE_DATA, FILE_WRITE_EA, READ_CONTROL,
            SYNCHRONIZE, WRITE_DAC, WRITE_OWNER,
        },
        System::{
            SystemServices::ACCESS_SYSTEM_SECURITY,
            Threading::{GetCurrentProcess, OpenProcessToken},
        },
    },
};

const FULL_SECURITY_INFORMATION: u32 = OWNER_SECURITY_INFORMATION
    | GROUP_SECURITY_INFORMATION
    | DACL_SECURITY_INFORMATION
    | LABEL_SECURITY_INFORMATION;
const SEAL_SECURITY_INFORMATION: u32 =
    DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION | LABEL_SECURITY_INFORMATION;
const MAX_SMALL_SEALABLE_FILE_BYTES: u64 = 64 * 1024;
const MAX_LEDGER_SEALABLE_FILE_BYTES: u64 = MAX_AUTHORITY_BINARY_BYTES;
const HASH_BUFFER_BYTES: usize = 16 * 1024;
const MUTATING_FILE_ACCESS: u32 = DELETE
    | WRITE_DAC
    | WRITE_OWNER
    | FILE_WRITE_DATA
    | FILE_APPEND_DATA
    | FILE_WRITE_EA
    | FILE_DELETE_CHILD
    | FILE_WRITE_ATTRIBUTES;
const GENERIC_ACCESS_MASK: u32 = 0xf000_0000;
const ROOT_READ_CAPABILITY_ACCESS: u32 = READ_CONTROL
    | SYNCHRONIZE
    | FILE_READ_DATA
    | FILE_EXECUTE
    | FILE_READ_ATTRIBUTES
    | FILE_READ_EA
    | ACCESS_SYSTEM_SECURITY;
const GENERATION_ROOT_SEAL_CAPABILITY_ACCESS: u32 = ROOT_READ_CAPABILITY_ACCESS | WRITE_DAC;
const PROGRESS_ROOT_CAPABILITY_ACCESS: u32 = FINALIZER_COMMIT_TRANSACTION_PROGRESS_HANDLE_ACCESS;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum FinalizerSealTarget {
    GenerationDirectory,
    RuntimeBlobDirectory,
    RuntimeBlobFile,
    BinaryFile,
    RuntimeSourceManifestFile,
    ImmutableStateFile,
    LedgerFile,
    WorkerNonceFile,
    CandidateConsumptionFile,
}

impl FinalizerSealTarget {
    pub(crate) const fn object_type(self) -> FinalizerSealedObjectType {
        match self {
            Self::GenerationDirectory | Self::RuntimeBlobDirectory => {
                FinalizerSealedObjectType::Directory
            }
            Self::BinaryFile
            | Self::RuntimeSourceManifestFile
            | Self::ImmutableStateFile
            | Self::LedgerFile
            | Self::WorkerNonceFile
            | Self::CandidateConsumptionFile => FinalizerSealedObjectType::File,
            Self::RuntimeBlobFile => FinalizerSealedObjectType::File,
        }
    }

    pub(crate) const fn maximum_byte_length(self) -> Option<u64> {
        match self {
            Self::GenerationDirectory | Self::RuntimeBlobDirectory => None,
            Self::RuntimeBlobFile => Some(MAX_LEDGER_SEALABLE_FILE_BYTES),
            Self::BinaryFile => Some(MAX_AUTHORITY_BINARY_BYTES),
            Self::RuntimeSourceManifestFile => Some(MAX_RUNTIME_SOURCE_MANIFEST_BYTES),
            Self::LedgerFile => Some(MAX_LEDGER_SEALABLE_FILE_BYTES),
            Self::ImmutableStateFile | Self::WorkerNonceFile | Self::CandidateConsumptionFile => {
                Some(MAX_SMALL_SEALABLE_FILE_BYTES)
            }
        }
    }

    pub(crate) const fn exact_security_transition(self) -> (&'static str, &'static str) {
        match self {
            Self::GenerationDirectory => (GENERATION_STAGING_SDDL, GENERATION_SEALED_SDDL),
            Self::RuntimeBlobDirectory => (
                RUNTIME_BLOB_DIRECTORY_STAGING_SDDL,
                RUNTIME_BLOB_DIRECTORY_FINAL_SDDL,
            ),
            Self::RuntimeBlobFile => (RUNTIME_BLOB_FILE_SDDL, RUNTIME_BLOB_FILE_SDDL),
            Self::BinaryFile => (BINARY_STAGING_SDDL, BINARY_SEALED_SDDL),
            Self::RuntimeSourceManifestFile => (STATE_STAGING_SDDL, STATE_IMMUTABLE_SDDL),
            Self::ImmutableStateFile => (STATE_STAGING_SDDL, STATE_IMMUTABLE_SDDL),
            Self::LedgerFile => (LEDGER_STAGING_SDDL, LEDGER_FINAL_SDDL),
            Self::WorkerNonceFile => (WORKER_NONCE_STAGING_SDDL, NONCE_SEALED_SDDL),
            Self::CandidateConsumptionFile => {
                (CANDIDATE_CONSUMPTION_STAGING_SDDL, NONCE_SEALED_SDDL)
            }
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum FinalizerSealedObjectType {
    File,
    Directory,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct PreSealStableIdentity {
    volume_serial: u64,
    file_id: [u8; 16],
    object_type: FinalizerSealedObjectType,
    link_count: u32,
    byte_length: u64,
    bytes_sha256: Option<[u8; 32]>,
}

impl PreSealStableIdentity {
    pub(crate) fn new_file(
        volume_serial: u64,
        file_id: [u8; 16],
        link_count: u32,
        byte_length: u64,
        bytes_sha256: [u8; 32],
    ) -> Result<Self, FinalizerSecurityError> {
        let value = Self {
            volume_serial,
            file_id,
            object_type: FinalizerSealedObjectType::File,
            link_count,
            byte_length,
            bytes_sha256: Some(bytes_sha256),
        };
        value.validate()?;
        Ok(value)
    }

    pub(crate) fn new_directory(
        volume_serial: u64,
        file_id: [u8; 16],
        link_count: u32,
        byte_length: u64,
    ) -> Result<Self, FinalizerSecurityError> {
        let value = Self {
            volume_serial,
            file_id,
            object_type: FinalizerSealedObjectType::Directory,
            link_count,
            byte_length,
            bytes_sha256: None,
        };
        value.validate()?;
        Ok(value)
    }

    pub(crate) fn volume_serial(&self) -> u64 {
        self.volume_serial
    }

    pub(crate) fn file_id(&self) -> &[u8; 16] {
        &self.file_id
    }

    pub(crate) fn object_type(&self) -> FinalizerSealedObjectType {
        self.object_type
    }

    pub(crate) fn link_count(&self) -> u32 {
        self.link_count
    }

    pub(crate) fn byte_length(&self) -> u64 {
        self.byte_length
    }

    pub(crate) fn bytes_sha256(&self) -> Option<&[u8; 32]> {
        self.bytes_sha256.as_ref()
    }

    fn validate(&self) -> Result<(), FinalizerSecurityError> {
        if self.volume_serial == 0
            || self.file_id.iter().all(|byte| *byte == 0)
            || self.link_count == 0
        {
            return Err(FinalizerSecurityError(
                "authority_finalizer_seal_identity_invalid",
            ));
        }
        match self.object_type {
            FinalizerSealedObjectType::File => {
                if self.link_count != 1
                    || self.byte_length == 0
                    || self.byte_length > MAX_AUTHORITY_BINARY_BYTES
                    || self.bytes_sha256.is_none()
                {
                    return Err(FinalizerSecurityError(
                        "authority_finalizer_seal_file_identity_invalid",
                    ));
                }
            }
            FinalizerSealedObjectType::Directory => {
                if self.bytes_sha256.is_some() {
                    return Err(FinalizerSecurityError(
                        "authority_finalizer_seal_directory_identity_invalid",
                    ));
                }
            }
        }
        Ok(())
    }
}

pub(crate) struct FinalizerSealedHandle {
    read_only_handle: OwnedHandle,
    receipt: FinalizerSecuritySealReceipt,
}

impl FinalizerSealedHandle {
    pub(crate) fn receipt(&self) -> &FinalizerSecuritySealReceipt {
        &self.receipt
    }

    pub(crate) fn into_read_only_handle(self) -> OwnedHandle {
        self.read_only_handle
    }

    pub(crate) fn read_only_handle(&self) -> &OwnedHandle {
        &self.read_only_handle
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct FinalizerSecuritySealReceipt {
    target: FinalizerSealTarget,
    stable_identity: PreSealStableIdentity,
    staging_security_sha256: [u8; 32],
    sealed_security_sha256: [u8; 32],
    write_handle_closed_before_reopen: bool,
    read_only_reopen_verified: bool,
    recovered_exact_sealed_after_restart: bool,
}

impl FinalizerSecuritySealReceipt {
    pub(crate) fn target(&self) -> FinalizerSealTarget {
        self.target
    }

    pub(crate) fn stable_identity(&self) -> &PreSealStableIdentity {
        &self.stable_identity
    }

    pub(crate) fn staging_security_sha256(&self) -> &[u8; 32] {
        &self.staging_security_sha256
    }

    pub(crate) fn sealed_security_sha256(&self) -> &[u8; 32] {
        &self.sealed_security_sha256
    }

    pub(crate) fn write_handle_closed_before_reopen(&self) -> bool {
        self.write_handle_closed_before_reopen
    }

    pub(crate) fn read_only_reopen_verified(&self) -> bool {
        self.read_only_reopen_verified
    }

    pub(crate) fn recovered_exact_sealed_after_restart(&self) -> bool {
        self.recovered_exact_sealed_after_restart
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum FinalizerObservedSealPhase {
    ExactStaging,
    ExactSealed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum FinalizerRootCapabilityKind {
    GenerationDirectory,
    GenerationSealProgressRoot,
    ActivationManifestNamespace,
    WorkerNonceNamespace,
    CandidateConsumptionNamespace,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum FinalizerRootSecurityPhase {
    ExactStaging,
    ExactSealed,
    ExactNamespace,
    ExactProgressNamespace,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct FinalizerRootCapabilityReadback {
    kind: FinalizerRootCapabilityKind,
    identity: PreSealStableIdentity,
    granted_access: u32,
    security_sha256: [u8; 32],
    security_phase: FinalizerRootSecurityPhase,
}

impl FinalizerRootCapabilityReadback {
    pub(crate) fn kind(&self) -> FinalizerRootCapabilityKind {
        self.kind
    }

    pub(crate) fn identity(&self) -> PreSealStableIdentity {
        self.identity
    }

    pub(crate) fn granted_access(&self) -> u32 {
        self.granted_access
    }

    pub(crate) fn security_sha256(&self) -> [u8; 32] {
        self.security_sha256
    }

    pub(crate) fn security_phase(&self) -> FinalizerRootSecurityPhase {
        self.security_phase
    }
}

/// Authenticates an already-open root handle as a least-privilege capability.
/// Shared namespace handles must be exactly read/traverse-only. Generation
/// directory handles may additionally carry WRITE_DAC until that directory is
/// sealed. Kernel-granted access and the complete canonical descriptor are
/// read from the handle itself; caller-provided claims are never accepted.
pub(crate) fn authenticate_finalizer_root_capability(
    handle: &OwnedHandle,
    kind: FinalizerRootCapabilityKind,
) -> Result<FinalizerRootCapabilityReadback, FinalizerSecurityError> {
    let identity =
        capture_preseal_identity_for_target(handle, FinalizerSealTarget::GenerationDirectory)?;
    let actual_access = granted_access(handle)?;
    let mut security_privilege = SecurityPrivilegeGuard::enable()?;
    let actual_security = read_handle_security(handle)?;
    security_privilege.restore()?;
    let actual_security_sha256 = sha256(actual_security.as_bytes());
    let security_phase =
        validate_root_capability_observation(kind, actual_access, actual_security_sha256)?;
    Ok(FinalizerRootCapabilityReadback {
        kind,
        identity,
        granted_access: actual_access,
        security_sha256: actual_security_sha256,
        security_phase,
    })
}

fn validate_root_capability_observation(
    kind: FinalizerRootCapabilityKind,
    actual_access: u32,
    actual_security_sha256: [u8; 32],
) -> Result<FinalizerRootSecurityPhase, FinalizerSecurityError> {
    let (security_phase, expected_security_sha256) = match kind {
        FinalizerRootCapabilityKind::GenerationDirectory => {
            let (staging, sealed) =
                expected_security_digests(FinalizerSealTarget::GenerationDirectory)?;
            if actual_security_sha256 == staging {
                (FinalizerRootSecurityPhase::ExactStaging, staging)
            } else if actual_security_sha256 == sealed {
                (FinalizerRootSecurityPhase::ExactSealed, sealed)
            } else {
                return Err(FinalizerSecurityError(
                    "authority_finalizer_root_capability_security_invalid",
                ));
            }
        }
        FinalizerRootCapabilityKind::GenerationSealProgressRoot => (
            FinalizerRootSecurityPhase::ExactProgressNamespace,
            canonical_security_sha256(FINALIZER_COMMIT_TRANSACTION_ROOT_SDDL)?,
        ),
        FinalizerRootCapabilityKind::ActivationManifestNamespace => (
            FinalizerRootSecurityPhase::ExactNamespace,
            canonical_security_sha256(STABLE_ROOT_SDDL)?,
        ),
        FinalizerRootCapabilityKind::WorkerNonceNamespace => (
            FinalizerRootSecurityPhase::ExactNamespace,
            canonical_security_sha256(WORKER_NONCE_NAMESPACE_SDDL)?,
        ),
        FinalizerRootCapabilityKind::CandidateConsumptionNamespace => (
            FinalizerRootSecurityPhase::ExactNamespace,
            canonical_security_sha256(CANDIDATE_CONSUMPTION_NAMESPACE_SDDL)?,
        ),
    };
    if actual_security_sha256 != expected_security_sha256 {
        return Err(FinalizerSecurityError(
            "authority_finalizer_root_capability_security_invalid",
        ));
    }
    let expected_access = match security_phase {
        FinalizerRootSecurityPhase::ExactStaging => GENERATION_ROOT_SEAL_CAPABILITY_ACCESS,
        FinalizerRootSecurityPhase::ExactSealed | FinalizerRootSecurityPhase::ExactNamespace => {
            ROOT_READ_CAPABILITY_ACCESS
        }
        FinalizerRootSecurityPhase::ExactProgressNamespace => PROGRESS_ROOT_CAPABILITY_ACCESS,
    };
    if actual_access != expected_access || actual_access & GENERIC_ACCESS_MASK != 0 {
        return Err(FinalizerSecurityError(
            "authority_finalizer_root_capability_access_invalid",
        ));
    }
    if !matches!(
        security_phase,
        FinalizerRootSecurityPhase::ExactStaging
            | FinalizerRootSecurityPhase::ExactProgressNamespace
    ) && actual_access & MUTATING_FILE_ACCESS != 0
    {
        return Err(FinalizerSecurityError(
            "authority_finalizer_root_capability_mutation_rights",
        ));
    }
    Ok(security_phase)
}

/// Owns the exact staging descriptor used for a create-new seal-progress file.
/// Keeping the allocation opaque prevents native callers from substituting a
/// caller-assembled descriptor while the object is being created.
pub(crate) struct FinalizerStagingSecurityDescriptor(SecurityDescriptor);

impl FinalizerStagingSecurityDescriptor {
    pub(crate) fn for_target(target: FinalizerSealTarget) -> Result<Self, FinalizerSecurityError> {
        let (staging, _) = target.exact_security_transition();
        Ok(Self(SecurityDescriptor::from_sddl(staging)?))
    }

    pub(crate) fn as_ptr(&self) -> *mut core::ffi::c_void {
        self.0 .0.cast()
    }
}

fn canonical_security_sha256(sddl: &str) -> Result<[u8; 32], FinalizerSecurityError> {
    let descriptor = SecurityDescriptor::from_sddl(sddl)?;
    let canonical = descriptor_sddl(descriptor.0, FULL_SECURITY_INFORMATION)?;
    Ok(sha256(canonical.as_bytes()))
}

pub(crate) fn expected_security_digests(
    target: FinalizerSealTarget,
) -> Result<([u8; 32], [u8; 32]), FinalizerSecurityError> {
    let (staging, sealed) = target.exact_security_transition();
    if target == FinalizerSealTarget::RuntimeBlobFile {
        let exact = canonical_security_sha256(sealed)?;
        return Ok((exact, exact));
    }
    let transition = SecurityTransition::new(staging, sealed)?;
    Ok((
        transition.staging_security_sha256,
        transition.sealed_security_sha256,
    ))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum FinalizerPublicationSecurityPhase {
    Staging,
    PrivateSealed,
    PublishedImmutable,
}

impl FinalizerPublicationSecurityPhase {
    const fn sddl(self) -> &'static str {
        match self {
            Self::Staging => STATE_STAGING_SDDL,
            Self::PrivateSealed => FINALIZER_PUBLISHING_PRIVATE_SEALED_SDDL,
            Self::PublishedImmutable => FINALIZER_COMMIT_RECEIPT_IMMUTABLE_SDDL,
        }
    }
}

pub(crate) fn expected_publication_security_sha256(
    phase: FinalizerPublicationSecurityPhase,
) -> Result<[u8; 32], FinalizerSecurityError> {
    canonical_security_sha256(phase.sddl())
}

pub(crate) fn transition_publication_security(
    handle: &OwnedHandle,
    from: FinalizerPublicationSecurityPhase,
    to: FinalizerPublicationSecurityPhase,
) -> Result<[u8; 32], FinalizerSecurityError> {
    if !matches!(
        (from, to),
        (
            FinalizerPublicationSecurityPhase::Staging,
            FinalizerPublicationSecurityPhase::PrivateSealed
        ) | (
            FinalizerPublicationSecurityPhase::PrivateSealed,
            FinalizerPublicationSecurityPhase::PublishedImmutable
        )
    ) {
        return Err(FinalizerSecurityError(
            "authority_finalizer_publication_security_transition_invalid",
        ));
    }
    let transition = SecurityTransition::new(from.sddl(), to.sddl())?;
    with_finalizer_security_privilege(|| {
        if read_handle_security(handle)? != transition.staging_canonical {
            return Err(FinalizerSecurityError(
                "authority_finalizer_publication_security_source_mismatch",
            ));
        }
        let (dacl, mandatory_label) = transition.sealed_descriptor.acl_parts()?;
        if unsafe {
            SetSecurityInfo(
                handle.as_raw_handle().cast(),
                SE_FILE_OBJECT,
                SEAL_SECURITY_INFORMATION,
                ptr::null_mut(),
                ptr::null_mut(),
                dacl,
                mandatory_label,
            )
        } != 0
        {
            return Err(FinalizerSecurityError(
                "authority_finalizer_publication_security_transition_failed",
            ));
        }
        if read_handle_security(handle)? != transition.sealed_canonical {
            return Err(FinalizerSecurityError(
                "authority_finalizer_publication_security_target_mismatch",
            ));
        }
        Ok(transition.sealed_security_sha256)
    })
}

pub(crate) fn verify_reopened_sealed_object(
    handle: &OwnedHandle,
    target: FinalizerSealTarget,
    expected_identity: &PreSealStableIdentity,
    expected_sealed_security_sha256: &[u8; 32],
) -> Result<(), FinalizerSecurityError> {
    validate_identity_for_target(expected_identity, target)?;
    verify_read_only_handle_access(handle, target.object_type())?;
    verify_identity_and_contents_for_target(handle, expected_identity, target)?;
    let mut security_privilege = SecurityPrivilegeGuard::enable()?;
    let actual = read_handle_security(handle)?;
    security_privilege.restore()?;
    if sha256(actual.as_bytes()) != *expected_sealed_security_sha256 {
        return Err(FinalizerSecurityError(
            "authority_finalizer_reopened_security_mismatch",
        ));
    }
    Ok(())
}

pub(crate) fn observe_reopened_seal_phase(
    handle: &OwnedHandle,
    target: FinalizerSealTarget,
    expected_identity: &PreSealStableIdentity,
) -> Result<FinalizerObservedSealPhase, FinalizerSecurityError> {
    validate_identity_for_target(expected_identity, target)?;
    verify_read_only_handle_access(handle, target.object_type())?;
    verify_identity_and_contents_for_target(handle, expected_identity, target)?;
    let mut security_privilege = SecurityPrivilegeGuard::enable()?;
    let actual = sha256(read_handle_security(handle)?.as_bytes());
    security_privilege.restore()?;
    let (staging, sealed) = expected_security_digests(target)?;
    if actual == staging {
        Ok(FinalizerObservedSealPhase::ExactStaging)
    } else if actual == sealed {
        Ok(FinalizerObservedSealPhase::ExactSealed)
    } else {
        Err(FinalizerSecurityError(
            "authority_finalizer_recovery_security_phase_invalid",
        ))
    }
}

pub(crate) fn recover_reopened_exact_sealed_object(
    read_only_handle: OwnedHandle,
    target: FinalizerSealTarget,
    expected_identity: PreSealStableIdentity,
) -> Result<FinalizerSealedHandle, FinalizerSecurityError> {
    let (staging_security_sha256, sealed_security_sha256) = expected_security_digests(target)?;
    verify_reopened_sealed_object(
        &read_only_handle,
        target,
        &expected_identity,
        &sealed_security_sha256,
    )?;
    Ok(FinalizerSealedHandle {
        read_only_handle,
        receipt: FinalizerSecuritySealReceipt {
            target,
            stable_identity: expected_identity,
            staging_security_sha256,
            sealed_security_sha256,
            write_handle_closed_before_reopen: true,
            read_only_reopen_verified: true,
            recovered_exact_sealed_after_restart: true,
        },
    })
}

pub(crate) fn with_finalizer_security_privilege<T, F>(
    operation: F,
) -> Result<T, FinalizerSecurityError>
where
    F: FnOnce() -> Result<T, FinalizerSecurityError>,
{
    let mut privilege = SecurityPrivilegeGuard::enable()?;
    let result = operation();
    let restored = privilege.restore();
    complete_security_privilege_scope(result, restored)
}

fn complete_security_privilege_scope<T>(
    result: Result<T, FinalizerSecurityError>,
    restored: Result<(), FinalizerSecurityError>,
) -> Result<T, FinalizerSecurityError> {
    match (result, restored) {
        (Ok(value), Ok(())) => Ok(value),
        (_, Err(restore_error)) => Err(restore_error),
        (Err(operation_error), Ok(())) => Err(operation_error),
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct FinalizerSecurityError(&'static str);

impl FinalizerSecurityError {
    pub(super) const fn new(code: &'static str) -> Self {
        Self(code)
    }

    pub(crate) fn code(&self) -> &'static str {
        self.0
    }
}

impl fmt::Display for FinalizerSecurityError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.0)
    }
}

impl std::error::Error for FinalizerSecurityError {}

/// Seals one already-authenticated object and returns only a verified,
/// read-only reopened handle.
///
/// `reopen_read_only` is deliberately invoked after `sealing_handle` has been
/// consumed and dropped. The reopened handle's kernel-granted access mask is
/// checked, so a caller cannot satisfy the contract by returning another
/// writable or WRITE_DAC-capable handle.
pub(crate) fn seal_held_object<F>(
    sealing_handle: OwnedHandle,
    target: FinalizerSealTarget,
    expected_staging_sddl: &str,
    target_sealed_sddl: &str,
    expected_identity: PreSealStableIdentity,
    reopen_read_only: F,
) -> Result<FinalizerSealedHandle, FinalizerSecurityError>
where
    F: FnOnce() -> Result<OwnedHandle, FinalizerSecurityError>,
{
    expected_identity.validate()?;
    if target.object_type() != expected_identity.object_type {
        return Err(FinalizerSecurityError(
            "authority_finalizer_seal_target_identity_mismatch",
        ));
    }
    validate_identity_for_target(&expected_identity, target)?;
    validate_target_policy(target, expected_staging_sddl, target_sealed_sddl)?;

    let transition = SecurityTransition::new(expected_staging_sddl, target_sealed_sddl)?;
    let mut security_privilege = SecurityPrivilegeGuard::enable()?;
    let verified = StagingHandle::verify(sealing_handle, target, expected_identity, transition)?;
    let changed = verified.apply_security()?;
    let sealed = changed.verify_sealed()?;
    let closed = sealed.close_write_handle();
    let reopened = closed.reopen_read_only(reopen_read_only)?;
    security_privilege.restore()?;
    Ok(reopened)
}

fn validate_target_policy(
    target: FinalizerSealTarget,
    expected_staging_sddl: &str,
    target_sealed_sddl: &str,
) -> Result<(), FinalizerSecurityError> {
    let (expected_staging, expected_sealed) = target.exact_security_transition();
    if expected_staging_sddl != expected_staging || target_sealed_sddl != expected_sealed {
        return Err(FinalizerSecurityError(
            "authority_finalizer_seal_policy_mismatch",
        ));
    }
    Ok(())
}

fn validate_identity_for_target(
    identity: &PreSealStableIdentity,
    target: FinalizerSealTarget,
) -> Result<(), FinalizerSecurityError> {
    identity.validate()?;
    if identity.object_type != target.object_type()
        || target
            .maximum_byte_length()
            .is_some_and(|maximum| identity.byte_length > maximum)
    {
        return Err(FinalizerSecurityError(
            "authority_finalizer_seal_target_identity_mismatch",
        ));
    }
    Ok(())
}

pub(crate) fn capture_preseal_identity_for_target(
    handle: &OwnedHandle,
    target: FinalizerSealTarget,
) -> Result<PreSealStableIdentity, FinalizerSecurityError> {
    let observed = observe_identity(handle)?;
    if observed.object_type != target.object_type() {
        return Err(FinalizerSecurityError(
            "authority_finalizer_seal_target_identity_mismatch",
        ));
    }
    match observed.object_type {
        FinalizerSealedObjectType::File => PreSealStableIdentity::new_file(
            observed.volume_serial,
            observed.file_id,
            observed.link_count,
            observed.byte_length,
            hash_held_file(
                handle,
                observed.byte_length,
                target.maximum_byte_length().ok_or(FinalizerSecurityError(
                    "authority_finalizer_seal_target_identity_mismatch",
                ))?,
            )?,
        ),
        FinalizerSealedObjectType::Directory => PreSealStableIdentity::new_directory(
            observed.volume_serial,
            observed.file_id,
            observed.link_count,
            observed.byte_length,
        ),
    }
}

pub(crate) fn capture_preseal_identity(
    handle: &OwnedHandle,
    object_type: FinalizerSealedObjectType,
) -> Result<PreSealStableIdentity, FinalizerSecurityError> {
    let observed = observe_identity(handle)?;
    match object_type {
        FinalizerSealedObjectType::File => PreSealStableIdentity::new_file(
            observed.volume_serial,
            observed.file_id,
            observed.link_count,
            observed.byte_length,
            hash_held_file(handle, observed.byte_length, MAX_AUTHORITY_BINARY_BYTES)?,
        ),
        FinalizerSealedObjectType::Directory => PreSealStableIdentity::new_directory(
            observed.volume_serial,
            observed.file_id,
            observed.link_count,
            observed.byte_length,
        ),
    }
}

struct SecurityTransition {
    staging_canonical: String,
    sealed_canonical: String,
    staging_security_sha256: [u8; 32],
    sealed_security_sha256: [u8; 32],
    sealed_descriptor: SecurityDescriptor,
}

impl SecurityTransition {
    fn new(staging_sddl: &str, sealed_sddl: &str) -> Result<Self, FinalizerSecurityError> {
        let staging_descriptor = SecurityDescriptor::from_sddl(staging_sddl)?;
        let sealed_descriptor = SecurityDescriptor::from_sddl(sealed_sddl)?;
        let staging_canonical = descriptor_sddl(staging_descriptor.0, FULL_SECURITY_INFORMATION)?;
        let sealed_canonical = descriptor_sddl(sealed_descriptor.0, FULL_SECURITY_INFORMATION)?;

        let staging_sections = descriptor_sections(&staging_canonical)?;
        let sealed_sections = descriptor_sections(&sealed_canonical)?;
        if staging_canonical == sealed_canonical {
            return Err(FinalizerSecurityError(
                "authority_finalizer_seal_transition_noop",
            ));
        }

        if staging_sections.owner != sealed_sections.owner
            || staging_sections.group != sealed_sections.group
            || staging_sections.sacl != sealed_sections.sacl
        {
            return Err(FinalizerSecurityError(
                "authority_finalizer_seal_immutable_security_changed",
            ));
        }
        if staging_sections.dacl == sealed_sections.dacl {
            return Err(FinalizerSecurityError(
                "authority_finalizer_seal_dacl_unchanged",
            ));
        }

        Ok(Self {
            staging_security_sha256: sha256(staging_canonical.as_bytes()),
            sealed_security_sha256: sha256(sealed_canonical.as_bytes()),
            staging_canonical,
            sealed_canonical,
            sealed_descriptor,
        })
    }
}

struct StagingHandle {
    handle: OwnedHandle,
    target: FinalizerSealTarget,
    identity: PreSealStableIdentity,
    transition: SecurityTransition,
}

impl StagingHandle {
    fn verify(
        handle: OwnedHandle,
        target: FinalizerSealTarget,
        identity: PreSealStableIdentity,
        transition: SecurityTransition,
    ) -> Result<Self, FinalizerSecurityError> {
        verify_sealing_handle_access(&handle, target.object_type())?;
        verify_identity_and_contents_for_target(&handle, &identity, target)?;
        let actual = read_handle_security(&handle)?;
        if actual != transition.staging_canonical {
            return Err(FinalizerSecurityError(
                "authority_finalizer_staging_security_mismatch",
            ));
        }
        verify_identity_and_contents_for_target(&handle, &identity, target)?;
        Ok(Self {
            handle,
            target,
            identity,
            transition,
        })
    }

    fn apply_security(self) -> Result<ChangedHandle, FinalizerSecurityError> {
        if SEAL_SECURITY_INFORMATION & (OWNER_SECURITY_INFORMATION | GROUP_SECURITY_INFORMATION)
            != 0
        {
            return Err(FinalizerSecurityError(
                "authority_finalizer_seal_scope_invalid",
            ));
        }
        let (dacl, mandatory_label) = self.transition.sealed_descriptor.acl_parts()?;
        if unsafe {
            SetSecurityInfo(
                self.handle.as_raw_handle().cast(),
                SE_FILE_OBJECT,
                SEAL_SECURITY_INFORMATION,
                ptr::null_mut(),
                ptr::null_mut(),
                dacl,
                mandatory_label,
            )
        } != 0
        {
            return Err(FinalizerSecurityError(
                "authority_finalizer_security_seal_failed",
            ));
        }
        Ok(ChangedHandle {
            handle: self.handle,
            target: self.target,
            identity: self.identity,
            transition: self.transition,
        })
    }
}

struct ChangedHandle {
    handle: OwnedHandle,
    target: FinalizerSealTarget,
    identity: PreSealStableIdentity,
    transition: SecurityTransition,
}

impl ChangedHandle {
    fn verify_sealed(self) -> Result<VerifiedSealedHandle, FinalizerSecurityError> {
        let actual = read_handle_security(&self.handle)?;
        if actual != self.transition.sealed_canonical {
            return Err(FinalizerSecurityError(
                "authority_finalizer_sealed_security_mismatch",
            ));
        }
        verify_identity_and_contents_for_target(&self.handle, &self.identity, self.target)?;
        Ok(VerifiedSealedHandle {
            handle: self.handle,
            target: self.target,
            identity: self.identity,
            staging_security_sha256: self.transition.staging_security_sha256,
            sealed_security_sha256: self.transition.sealed_security_sha256,
        })
    }
}

struct VerifiedSealedHandle {
    handle: OwnedHandle,
    target: FinalizerSealTarget,
    identity: PreSealStableIdentity,
    staging_security_sha256: [u8; 32],
    sealed_security_sha256: [u8; 32],
}

impl VerifiedSealedHandle {
    fn close_write_handle(self) -> ClosedSeal {
        let Self {
            handle,
            target,
            identity,
            staging_security_sha256,
            sealed_security_sha256,
        } = self;
        // Security descriptor updates have no file-data flush contract. The
        // durable boundary here is the kernel-handle close followed by a new
        // read-only open and complete descriptor readback below.
        drop(handle);
        ClosedSeal {
            target,
            identity,
            staging_security_sha256,
            sealed_security_sha256,
        }
    }
}

struct ClosedSeal {
    target: FinalizerSealTarget,
    identity: PreSealStableIdentity,
    staging_security_sha256: [u8; 32],
    sealed_security_sha256: [u8; 32],
}

impl ClosedSeal {
    fn reopen_read_only<F>(self, reopen: F) -> Result<FinalizerSealedHandle, FinalizerSecurityError>
    where
        F: FnOnce() -> Result<OwnedHandle, FinalizerSecurityError>,
    {
        let handle = reopen()?;
        verify_read_only_handle_access(&handle, self.identity.object_type)?;
        verify_identity_and_contents_for_target(&handle, &self.identity, self.target)?;
        let actual = read_handle_security(&handle)?;
        if sha256(actual.as_bytes()) != self.sealed_security_sha256 {
            return Err(FinalizerSecurityError(
                "authority_finalizer_reopened_security_mismatch",
            ));
        }
        Ok(FinalizerSealedHandle {
            read_only_handle: handle,
            receipt: FinalizerSecuritySealReceipt {
                target: self.target,
                stable_identity: self.identity,
                staging_security_sha256: self.staging_security_sha256,
                sealed_security_sha256: self.sealed_security_sha256,
                write_handle_closed_before_reopen: true,
                read_only_reopen_verified: true,
                recovered_exact_sealed_after_restart: false,
            },
        })
    }
}

fn verify_sealing_handle_access(
    handle: &OwnedHandle,
    object_type: FinalizerSealedObjectType,
) -> Result<(), FinalizerSecurityError> {
    validate_sealing_access(granted_access(handle)?, object_type)
}

fn validate_sealing_access(
    access: u32,
    object_type: FinalizerSealedObjectType,
) -> Result<(), FinalizerSecurityError> {
    let common = READ_CONTROL
        | WRITE_DAC
        | SYNCHRONIZE
        | FILE_READ_ATTRIBUTES
        | FILE_READ_EA
        | ACCESS_SYSTEM_SECURITY;
    let allowed = match object_type {
        FinalizerSealedObjectType::File => common | FILE_READ_DATA,
        FinalizerSealedObjectType::Directory => common | FILE_READ_DATA | FILE_EXECUTE,
    };
    if access & common != common || access & !allowed != 0 {
        return Err(FinalizerSecurityError(
            "authority_finalizer_sealing_handle_access_invalid",
        ));
    }
    Ok(())
}

fn verify_read_only_handle_access(
    handle: &OwnedHandle,
    object_type: FinalizerSealedObjectType,
) -> Result<(), FinalizerSecurityError> {
    validate_read_only_access(granted_access(handle)?, object_type)
}

fn validate_read_only_access(
    access: u32,
    object_type: FinalizerSealedObjectType,
) -> Result<(), FinalizerSecurityError> {
    let common =
        READ_CONTROL | SYNCHRONIZE | FILE_READ_ATTRIBUTES | FILE_READ_EA | ACCESS_SYSTEM_SECURITY;
    let allowed = match object_type {
        FinalizerSealedObjectType::File => common | FILE_READ_DATA,
        FinalizerSealedObjectType::Directory => common | FILE_READ_DATA | FILE_EXECUTE,
    };
    if access & common != common
        || access & !allowed != 0
        || access & (MUTATING_FILE_ACCESS | GENERIC_ACCESS_MASK) != 0
    {
        return Err(FinalizerSecurityError(
            "authority_finalizer_reopened_handle_not_read_only",
        ));
    }
    Ok(())
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

fn granted_access(handle: &OwnedHandle) -> Result<u32, FinalizerSecurityError> {
    let raw: HANDLE = handle.as_raw_handle().cast();
    if raw.is_null() || raw == INVALID_HANDLE_VALUE {
        return Err(FinalizerSecurityError(
            "authority_finalizer_seal_handle_invalid",
        ));
    }
    let mut information = unsafe { zeroed::<PublicObjectBasicInformation>() };
    let mut returned = 0u32;
    let status = unsafe {
        NtQueryObject(
            raw,
            ObjectBasicInformation,
            (&mut information as *mut PublicObjectBasicInformation).cast(),
            size_of::<PublicObjectBasicInformation>() as u32,
            &mut returned,
        )
    };
    if status < 0 || returned < (size_of::<u32>() * 2) as u32 {
        return Err(FinalizerSecurityError(
            "authority_finalizer_seal_handle_access_unavailable",
        ));
    }
    Ok(information.granted_access)
}

fn verify_identity_and_contents(
    handle: &OwnedHandle,
    expected: &PreSealStableIdentity,
) -> Result<(), FinalizerSecurityError> {
    verify_identity_and_contents_with_maximum(handle, expected, MAX_AUTHORITY_BINARY_BYTES)
}

fn verify_identity_and_contents_for_target(
    handle: &OwnedHandle,
    expected: &PreSealStableIdentity,
    target: FinalizerSealTarget,
) -> Result<(), FinalizerSecurityError> {
    validate_identity_for_target(expected, target)?;
    verify_identity_and_contents_with_maximum(
        handle,
        expected,
        target.maximum_byte_length().unwrap_or(0),
    )
}

fn verify_identity_and_contents_with_maximum(
    handle: &OwnedHandle,
    expected: &PreSealStableIdentity,
    maximum_byte_length: u64,
) -> Result<(), FinalizerSecurityError> {
    let actual = observe_identity(handle)?;
    if actual.volume_serial != expected.volume_serial
        || actual.file_id != expected.file_id
        || actual.object_type != expected.object_type
        || actual.link_count != expected.link_count
        || actual.byte_length != expected.byte_length
    {
        return Err(FinalizerSecurityError(
            "authority_finalizer_seal_identity_mismatch",
        ));
    }
    if let Some(expected_hash) = expected.bytes_sha256 {
        if hash_held_file(handle, actual.byte_length, maximum_byte_length)? != expected_hash {
            return Err(FinalizerSecurityError(
                "authority_finalizer_seal_bytes_mismatch",
            ));
        }
        if observe_identity(handle)? != actual {
            return Err(FinalizerSecurityError(
                "authority_finalizer_seal_identity_changed_during_read",
            ));
        }
    }
    Ok(())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct ObservedIdentity {
    volume_serial: u64,
    file_id: [u8; 16],
    object_type: FinalizerSealedObjectType,
    link_count: u32,
    byte_length: u64,
}

fn observe_identity(handle: &OwnedHandle) -> Result<ObservedIdentity, FinalizerSecurityError> {
    let raw: HANDLE = handle.as_raw_handle().cast();
    let mut information = unsafe { zeroed::<BY_HANDLE_FILE_INFORMATION>() };
    if raw.is_null()
        || raw == INVALID_HANDLE_VALUE
        || unsafe { GetFileInformationByHandle(raw, &mut information) } == 0
        || information.dwVolumeSerialNumber == 0
        || (information.nFileIndexHigh == 0 && information.nFileIndexLow == 0)
        || information.nNumberOfLinks == 0
        || information.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
    {
        return Err(FinalizerSecurityError(
            "authority_finalizer_seal_identity_unavailable",
        ));
    }
    let file_index =
        (u64::from(information.nFileIndexHigh) << 32) | u64::from(information.nFileIndexLow);
    let mut file_id = [0u8; 16];
    file_id[..8].copy_from_slice(&file_index.to_be_bytes());
    let object_type = if information.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY != 0 {
        FinalizerSealedObjectType::Directory
    } else {
        FinalizerSealedObjectType::File
    };
    Ok(ObservedIdentity {
        volume_serial: u64::from(information.dwVolumeSerialNumber),
        file_id,
        object_type,
        link_count: information.nNumberOfLinks,
        byte_length: (u64::from(information.nFileSizeHigh) << 32)
            | u64::from(information.nFileSizeLow),
    })
}

fn hash_held_file(
    handle: &OwnedHandle,
    expected_length: u64,
    maximum_byte_length: u64,
) -> Result<[u8; 32], FinalizerSecurityError> {
    if expected_length == 0
        || maximum_byte_length == 0
        || maximum_byte_length > MAX_AUTHORITY_BINARY_BYTES
        || expected_length > maximum_byte_length
    {
        return Err(FinalizerSecurityError(
            "authority_finalizer_seal_file_length_invalid",
        ));
    }
    let raw: HANDLE = handle.as_raw_handle().cast();
    if unsafe { SetFilePointerEx(raw, 0, ptr::null_mut(), FILE_BEGIN) } == 0 {
        return Err(FinalizerSecurityError(
            "authority_finalizer_seal_file_seek_failed",
        ));
    }
    let mut digest = Sha256::new();
    let mut total = 0u64;
    let mut buffer = [0u8; HASH_BUFFER_BYTES];
    loop {
        let mut read = 0u32;
        if unsafe {
            ReadFile(
                raw,
                buffer.as_mut_ptr(),
                buffer.len() as u32,
                &mut read,
                ptr::null_mut(),
            )
        } == 0
        {
            return Err(FinalizerSecurityError(
                "authority_finalizer_seal_file_read_failed",
            ));
        }
        if read == 0 {
            break;
        }
        total = total
            .checked_add(u64::from(read))
            .ok_or(FinalizerSecurityError(
                "authority_finalizer_seal_file_length_invalid",
            ))?;
        if total > expected_length {
            return Err(FinalizerSecurityError(
                "authority_finalizer_seal_file_length_mismatch",
            ));
        }
        digest.update(&buffer[..read as usize]);
    }
    if total != expected_length {
        return Err(FinalizerSecurityError(
            "authority_finalizer_seal_file_length_mismatch",
        ));
    }
    let bytes = digest.finalize();
    let mut output = [0u8; 32];
    output.copy_from_slice(&bytes);
    Ok(output)
}

fn read_handle_security(handle: &OwnedHandle) -> Result<String, FinalizerSecurityError> {
    let mut descriptor = ptr::null_mut();
    let status = unsafe {
        GetSecurityInfo(
            handle.as_raw_handle().cast(),
            SE_FILE_OBJECT,
            FULL_SECURITY_INFORMATION,
            ptr::null_mut(),
            ptr::null_mut(),
            ptr::null_mut(),
            ptr::null_mut(),
            &mut descriptor,
        )
    };
    if status != 0 || descriptor.is_null() {
        return Err(FinalizerSecurityError(
            "authority_finalizer_security_readback_failed",
        ));
    }
    let descriptor = SecurityDescriptor(descriptor);
    let canonical = descriptor_sddl(descriptor.0, FULL_SECURITY_INFORMATION)?;
    descriptor_sections(&canonical)?;
    Ok(canonical)
}

struct SecurityDescriptor(PSECURITY_DESCRIPTOR);

impl SecurityDescriptor {
    fn from_sddl(value: &str) -> Result<Self, FinalizerSecurityError> {
        if value.is_empty() || value.len() > 16 * 1024 || value.contains('\0') {
            return Err(FinalizerSecurityError(
                "authority_finalizer_security_descriptor_invalid",
            ));
        }
        let encoded = value
            .encode_utf16()
            .chain(std::iter::once(0))
            .collect::<Vec<_>>();
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
            return Err(FinalizerSecurityError(
                "authority_finalizer_security_descriptor_invalid",
            ));
        }
        Ok(Self(descriptor))
    }

    fn acl_parts(&self) -> Result<(*mut ACL, *mut ACL), FinalizerSecurityError> {
        let mut dacl_present = 0;
        let mut dacl_defaulted = 0;
        let mut dacl = ptr::null_mut();
        if unsafe {
            GetSecurityDescriptorDacl(self.0, &mut dacl_present, &mut dacl, &mut dacl_defaulted)
        } == 0
            || dacl_present == 0
            || dacl_defaulted != 0
            || dacl.is_null()
        {
            return Err(FinalizerSecurityError(
                "authority_finalizer_security_dacl_invalid",
            ));
        }

        let mut sacl_present = 0;
        let mut sacl_defaulted = 0;
        let mut sacl = ptr::null_mut();
        if unsafe {
            GetSecurityDescriptorSacl(self.0, &mut sacl_present, &mut sacl, &mut sacl_defaulted)
        } == 0
            || sacl_present == 0
            || sacl_defaulted != 0
            || sacl.is_null()
        {
            return Err(FinalizerSecurityError(
                "authority_finalizer_security_label_invalid",
            ));
        }
        Ok((dacl, sacl))
    }
}

impl Drop for SecurityDescriptor {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe {
                LocalFree(self.0.cast());
            }
        }
    }
}

struct SecurityPrivilegeGuard {
    token: OwnedHandle,
    previous: TOKEN_PRIVILEGES,
    restore_previous: bool,
}

impl SecurityPrivilegeGuard {
    fn enable() -> Result<Self, FinalizerSecurityError> {
        let mut token = ptr::null_mut();
        if unsafe {
            OpenProcessToken(
                GetCurrentProcess(),
                TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
                &mut token,
            )
        } == 0
            || token.is_null()
            || token == INVALID_HANDLE_VALUE
        {
            return Err(FinalizerSecurityError(
                "authority_finalizer_security_privilege_open_failed",
            ));
        }
        let token = unsafe { OwnedHandle::from_raw_handle(token as RawHandle) };
        let mut luid = LUID {
            LowPart: 0,
            HighPart: 0,
        };
        if unsafe { LookupPrivilegeValueW(ptr::null(), SE_SECURITY_NAME, &mut luid) } == 0 {
            return Err(FinalizerSecurityError(
                "authority_finalizer_security_privilege_lookup_failed",
            ));
        }
        let requested = TOKEN_PRIVILEGES {
            PrivilegeCount: 1,
            Privileges: [LUID_AND_ATTRIBUTES {
                Luid: luid,
                Attributes: SE_PRIVILEGE_ENABLED,
            }],
        };
        let mut previous = unsafe { zeroed::<TOKEN_PRIVILEGES>() };
        let mut previous_length = 0u32;
        unsafe {
            SetLastError(0);
        }
        if unsafe {
            AdjustTokenPrivileges(
                token.as_raw_handle().cast(),
                0,
                &requested,
                size_of::<TOKEN_PRIVILEGES>() as u32,
                &mut previous,
                &mut previous_length,
            )
        } == 0
            || unsafe { GetLastError() } == ERROR_NOT_ALL_ASSIGNED
            || previous.PrivilegeCount > 1
            || previous_length > size_of::<TOKEN_PRIVILEGES>() as u32
        {
            return Err(FinalizerSecurityError(
                "authority_finalizer_security_privilege_enable_failed",
            ));
        }
        Ok(Self {
            token,
            previous,
            restore_previous: previous.PrivilegeCount == 1,
        })
    }

    fn restore(&mut self) -> Result<(), FinalizerSecurityError> {
        if !self.restore_previous {
            return Ok(());
        }
        unsafe {
            SetLastError(0);
        }
        if unsafe {
            AdjustTokenPrivileges(
                self.token.as_raw_handle().cast(),
                0,
                &self.previous,
                0,
                ptr::null_mut(),
                ptr::null_mut(),
            )
        } == 0
            || unsafe { GetLastError() } == ERROR_NOT_ALL_ASSIGNED
        {
            return Err(FinalizerSecurityError(
                "authority_finalizer_security_privilege_restore_failed",
            ));
        }
        self.restore_previous = false;
        Ok(())
    }
}

impl Drop for SecurityPrivilegeGuard {
    fn drop(&mut self) {
        let _ = self.restore();
    }
}

fn descriptor_sddl(
    descriptor: PSECURITY_DESCRIPTOR,
    security_information: u32,
) -> Result<String, FinalizerSecurityError> {
    let mut text = ptr::null_mut::<u16>();
    let mut text_length = 0u32;
    let converted = !descriptor.is_null()
        && unsafe {
            ConvertSecurityDescriptorToStringSecurityDescriptorW(
                descriptor,
                SDDL_REVISION_1,
                security_information,
                &mut text,
                &mut text_length,
            )
        } != 0;
    if !converted || text.is_null() || text_length == 0 {
        if !text.is_null() {
            unsafe {
                LocalFree(text.cast());
            }
        }
        return Err(FinalizerSecurityError(
            "authority_finalizer_security_descriptor_projection_failed",
        ));
    }
    let mut words = unsafe { std::slice::from_raw_parts(text, text_length as usize) }.to_vec();
    unsafe {
        LocalFree(text.cast());
    }
    let terminator = words
        .iter()
        .position(|word| *word == 0)
        .ok_or(FinalizerSecurityError(
            "authority_finalizer_security_descriptor_projection_failed",
        ))?;
    if terminator == 0 || words[terminator..].iter().any(|word| *word != 0) {
        return Err(FinalizerSecurityError(
            "authority_finalizer_security_descriptor_projection_failed",
        ));
    }
    words.truncate(terminator);
    String::from_utf16(&words).map_err(|_| {
        FinalizerSecurityError("authority_finalizer_security_descriptor_projection_failed")
    })
}

struct DescriptorSections<'a> {
    owner: &'a str,
    group: &'a str,
    dacl: &'a str,
    sacl: &'a str,
}

fn descriptor_sections(value: &str) -> Result<DescriptorSections<'_>, FinalizerSecurityError> {
    let after_owner = value.strip_prefix("O:").ok_or(FinalizerSecurityError(
        "authority_finalizer_security_descriptor_incomplete",
    ))?;
    let (owner, after_group) = after_owner.split_once("G:").ok_or(FinalizerSecurityError(
        "authority_finalizer_security_descriptor_incomplete",
    ))?;
    let (group, after_dacl) = after_group.split_once("D:").ok_or(FinalizerSecurityError(
        "authority_finalizer_security_descriptor_incomplete",
    ))?;
    let (dacl, sacl) = after_dacl.split_once("S:").ok_or(FinalizerSecurityError(
        "authority_finalizer_security_descriptor_incomplete",
    ))?;
    if owner.is_empty()
        || group.is_empty()
        || !dacl.starts_with('P')
        || !dacl.contains("(A;")
        || !sacl.starts_with("(ML;")
        || sacl.matches("(ML;").count() != 1
        || sacl.matches('(').count() != 1
    {
        return Err(FinalizerSecurityError(
            "authority_finalizer_security_descriptor_incomplete",
        ));
    }
    Ok(DescriptorSections {
        owner,
        group,
        dacl,
        sacl,
    })
}

fn sha256(bytes: &[u8]) -> [u8; 32] {
    let digest = Sha256::digest(bytes);
    let mut output = [0u8; 32];
    output.copy_from_slice(&digest);
    output
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        fs::{self, OpenOptions},
        io::Write,
        os::windows::io::{FromRawHandle, IntoRawHandle},
        path::PathBuf,
        time::{SystemTime, UNIX_EPOCH},
    };

    const STAGING_FILE_SDDL: &str =
        "O:SYG:SYD:P(A;;0x001f01ff;;;SY)(A;;0x0017008f;;;BA)S:(ML;;NW;;;HI)";
    const SEALED_FILE_SDDL: &str =
        "O:SYG:SYD:P(A;;0x001f01ff;;;SY)(A;;0x00120089;;;BA)S:(ML;;NW;;;HI)";
    const SEALED_FILE_DIFFERENT_OWNER_SDDL: &str =
        "O:BAG:SYD:P(A;;0x001f01ff;;;SY)(A;;0x00120089;;;BA)S:(ML;;NW;;;HI)";
    const SEALED_FILE_DIFFERENT_LABEL_SDDL: &str =
        "O:SYG:SYD:P(A;;0x001f01ff;;;SY)(A;;0x00120089;;;BA)S:(ML;;NW;;;ME)";

    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    enum SealOrderEvent {
        StagingReadback,
        IdentityReadback,
        SecurityApplied,
        SealedReadback,
        WriteHandleClosed,
        ReadOnlyReopened,
        ReopenedReadback,
    }

    fn validate_order(events: &[SealOrderEvent]) -> Result<(), FinalizerSecurityError> {
        const REQUIRED: [SealOrderEvent; 7] = [
            SealOrderEvent::StagingReadback,
            SealOrderEvent::IdentityReadback,
            SealOrderEvent::SecurityApplied,
            SealOrderEvent::SealedReadback,
            SealOrderEvent::WriteHandleClosed,
            SealOrderEvent::ReadOnlyReopened,
            SealOrderEvent::ReopenedReadback,
        ];
        if events != REQUIRED {
            return Err(FinalizerSecurityError(
                "authority_finalizer_seal_order_invalid",
            ));
        }
        Ok(())
    }

    #[test]
    fn exact_mutation_scope_never_requests_owner_or_group() {
        assert_eq!(
            SEAL_SECURITY_INFORMATION,
            DACL_SECURITY_INFORMATION
                | PROTECTED_DACL_SECURITY_INFORMATION
                | LABEL_SECURITY_INFORMATION
        );
        assert_eq!(
            SEAL_SECURITY_INFORMATION & (OWNER_SECURITY_INFORMATION | GROUP_SECURITY_INFORMATION),
            0
        );
    }

    #[test]
    fn nonce_roles_are_bound_to_their_exact_staging_policy() {
        validate_target_policy(
            FinalizerSealTarget::WorkerNonceFile,
            WORKER_NONCE_STAGING_SDDL,
            NONCE_SEALED_SDDL,
        )
        .unwrap();
        validate_target_policy(
            FinalizerSealTarget::CandidateConsumptionFile,
            CANDIDATE_CONSUMPTION_STAGING_SDDL,
            NONCE_SEALED_SDDL,
        )
        .unwrap();
        assert_eq!(
            validate_target_policy(
                FinalizerSealTarget::WorkerNonceFile,
                CANDIDATE_CONSUMPTION_STAGING_SDDL,
                NONCE_SEALED_SDDL,
            )
            .unwrap_err()
            .code(),
            "authority_finalizer_seal_policy_mismatch"
        );
        assert!(validate_target_policy(
            FinalizerSealTarget::CandidateConsumptionFile,
            CANDIDATE_CONSUMPTION_STAGING_SDDL,
            WORKER_NONCE_STAGING_SDDL,
        )
        .is_err());
    }

    #[test]
    fn every_generation_role_is_bound_to_its_exact_policy_transition() {
        for target in [
            FinalizerSealTarget::GenerationDirectory,
            FinalizerSealTarget::RuntimeBlobDirectory,
            FinalizerSealTarget::BinaryFile,
            FinalizerSealTarget::ImmutableStateFile,
            FinalizerSealTarget::LedgerFile,
            FinalizerSealTarget::WorkerNonceFile,
            FinalizerSealTarget::CandidateConsumptionFile,
        ] {
            let (staging, sealed) = target.exact_security_transition();
            validate_target_policy(target, staging, sealed).unwrap();
            let (staging_digest, sealed_digest) = expected_security_digests(target).unwrap();
            assert_ne!(staging_digest, sealed_digest);
            assert_eq!(
                validate_target_policy(target, sealed, staging)
                    .unwrap_err()
                    .code(),
                "authority_finalizer_seal_policy_mismatch"
            );
        }
    }

    #[test]
    fn runtime_blob_file_uses_one_exact_creation_policy_without_a_seal_transition() {
        let (creation, runtime) =
            expected_security_digests(FinalizerSealTarget::RuntimeBlobFile).unwrap();
        assert_eq!(creation, runtime);
        assert_eq!(
            runtime,
            canonical_security_sha256(RUNTIME_BLOB_FILE_SDDL).unwrap()
        );
    }

    #[test]
    fn root_capabilities_bind_exact_access_and_complete_security() {
        let (generation_staging, generation_sealed) =
            expected_security_digests(FinalizerSealTarget::GenerationDirectory).unwrap();
        assert_eq!(
            validate_root_capability_observation(
                FinalizerRootCapabilityKind::GenerationDirectory,
                GENERATION_ROOT_SEAL_CAPABILITY_ACCESS,
                generation_staging,
            )
            .unwrap(),
            FinalizerRootSecurityPhase::ExactStaging
        );
        assert_eq!(
            validate_root_capability_observation(
                FinalizerRootCapabilityKind::GenerationDirectory,
                ROOT_READ_CAPABILITY_ACCESS,
                generation_sealed,
            )
            .unwrap(),
            FinalizerRootSecurityPhase::ExactSealed
        );
        assert_eq!(
            validate_root_capability_observation(
                FinalizerRootCapabilityKind::GenerationDirectory,
                GENERATION_ROOT_SEAL_CAPABILITY_ACCESS,
                generation_sealed,
            )
            .unwrap_err()
            .code(),
            "authority_finalizer_root_capability_access_invalid"
        );

        for (kind, sddl) in [
            (
                FinalizerRootCapabilityKind::ActivationManifestNamespace,
                STABLE_ROOT_SDDL,
            ),
            (
                FinalizerRootCapabilityKind::WorkerNonceNamespace,
                WORKER_NONCE_NAMESPACE_SDDL,
            ),
            (
                FinalizerRootCapabilityKind::CandidateConsumptionNamespace,
                CANDIDATE_CONSUMPTION_NAMESPACE_SDDL,
            ),
        ] {
            let security = canonical_security_sha256(sddl).unwrap();
            assert_eq!(
                validate_root_capability_observation(kind, ROOT_READ_CAPABILITY_ACCESS, security)
                    .unwrap(),
                FinalizerRootSecurityPhase::ExactNamespace
            );
            assert_eq!(
                validate_root_capability_observation(
                    kind,
                    ROOT_READ_CAPABILITY_ACCESS | WRITE_DAC,
                    security,
                )
                .unwrap_err()
                .code(),
                "authority_finalizer_root_capability_access_invalid"
            );
            assert_eq!(
                validate_root_capability_observation(kind, ROOT_READ_CAPABILITY_ACCESS, [99; 32],)
                    .unwrap_err()
                    .code(),
                "authority_finalizer_root_capability_security_invalid"
            );
        }

        let progress_security =
            canonical_security_sha256(FINALIZER_COMMIT_TRANSACTION_ROOT_SDDL).unwrap();
        assert!(FINALIZER_COMMIT_TRANSACTION_ROOT_SDDL.contains("(A;;0x001200ab;;;SY)"));
        assert!(FINALIZER_COMMIT_TRANSACTION_ROOT_SDDL.contains("(A;;0x001200ab;;;BA)"));
        assert_eq!(
            PROGRESS_ROOT_CAPABILITY_ACCESS,
            super::super::finalizer_generation_seal::PROGRESS_ROOT_ACCESS
        );
        let receipt_access =
            super::super::finalizer_commit_store_windows::AUTHENTICATED_TRANSACTION_ROOT_ACCESS;
        assert_eq!(receipt_access & !PROGRESS_ROOT_CAPABILITY_ACCESS, 0);
        assert_eq!(receipt_access & (FILE_READ_DATA | FILE_READ_EA), 0);
        assert_eq!(
            validate_root_capability_observation(
                FinalizerRootCapabilityKind::GenerationSealProgressRoot,
                PROGRESS_ROOT_CAPABILITY_ACCESS,
                progress_security,
            )
            .unwrap(),
            FinalizerRootSecurityPhase::ExactProgressNamespace
        );
        assert_eq!(
            validate_root_capability_observation(
                FinalizerRootCapabilityKind::GenerationSealProgressRoot,
                PROGRESS_ROOT_CAPABILITY_ACCESS,
                canonical_security_sha256(STABLE_ROOT_SDDL).unwrap(),
            )
            .unwrap_err()
            .code(),
            "authority_finalizer_root_capability_security_invalid"
        );
        for hostile_access in [
            ROOT_READ_CAPABILITY_ACCESS,
            PROGRESS_ROOT_CAPABILITY_ACCESS | WRITE_DAC,
            PROGRESS_ROOT_CAPABILITY_ACCESS | FILE_APPEND_DATA,
        ] {
            assert_eq!(
                validate_root_capability_observation(
                    FinalizerRootCapabilityKind::GenerationSealProgressRoot,
                    hostile_access,
                    progress_security,
                )
                .unwrap_err()
                .code(),
                "authority_finalizer_root_capability_access_invalid"
            );
        }
        assert_eq!(
            validate_root_capability_observation(
                FinalizerRootCapabilityKind::GenerationSealProgressRoot,
                PROGRESS_ROOT_CAPABILITY_ACCESS,
                [98; 32],
            )
            .unwrap_err()
            .code(),
            "authority_finalizer_root_capability_security_invalid"
        );

        assert_eq!(super::super::STATE_DIRECTORY_SDDL, STABLE_ROOT_SDDL);
        let activation_security = canonical_security_sha256(STABLE_ROOT_SDDL).unwrap();
        assert_eq!(
            validate_root_capability_observation(
                FinalizerRootCapabilityKind::ActivationManifestNamespace,
                ROOT_READ_CAPABILITY_ACCESS,
                activation_security,
            )
            .unwrap(),
            FinalizerRootSecurityPhase::ExactNamespace
        );
        assert_eq!(
            validate_root_capability_observation(
                FinalizerRootCapabilityKind::ActivationManifestNamespace,
                ROOT_READ_CAPABILITY_ACCESS,
                canonical_security_sha256(
                    super::super::security_policy::CANDIDATE_ACTIVATION_NAMESPACE_SDDL
                )
                .unwrap(),
            )
            .unwrap_err()
            .code(),
            "authority_finalizer_root_capability_security_invalid"
        );
    }

    #[test]
    fn binary_limit_does_not_expand_small_state_or_nonce_limits() {
        let large = PreSealStableIdentity::new_file(
            1,
            [1; 16],
            1,
            MAX_SMALL_SEALABLE_FILE_BYTES + 1,
            [2; 32],
        )
        .unwrap();
        validate_identity_for_target(&large, FinalizerSealTarget::BinaryFile).unwrap();
        validate_identity_for_target(&large, FinalizerSealTarget::LedgerFile).unwrap();
        for target in [
            FinalizerSealTarget::ImmutableStateFile,
            FinalizerSealTarget::WorkerNonceFile,
            FinalizerSealTarget::CandidateConsumptionFile,
        ] {
            assert_eq!(
                validate_identity_for_target(&large, target)
                    .unwrap_err()
                    .code(),
                "authority_finalizer_seal_target_identity_mismatch"
            );
        }
    }

    #[test]
    fn close_then_reopen_order_is_exact_and_hostile_permutations_fail() {
        use SealOrderEvent::*;
        let exact = [
            StagingReadback,
            IdentityReadback,
            SecurityApplied,
            SealedReadback,
            WriteHandleClosed,
            ReadOnlyReopened,
            ReopenedReadback,
        ];
        validate_order(&exact).unwrap();
        for hostile in [
            vec![
                StagingReadback,
                IdentityReadback,
                SecurityApplied,
                SealedReadback,
                ReadOnlyReopened,
                WriteHandleClosed,
                ReopenedReadback,
            ],
            vec![
                StagingReadback,
                IdentityReadback,
                SecurityApplied,
                WriteHandleClosed,
                ReadOnlyReopened,
                ReopenedReadback,
            ],
            vec![
                StagingReadback,
                IdentityReadback,
                SealedReadback,
                WriteHandleClosed,
                ReadOnlyReopened,
                ReopenedReadback,
            ],
        ] {
            assert_eq!(
                validate_order(&hostile).unwrap_err().code(),
                "authority_finalizer_seal_order_invalid"
            );
        }
    }

    #[test]
    fn descriptor_transition_preserves_owner_group_and_label() {
        let transition = SecurityTransition::new(STAGING_FILE_SDDL, SEALED_FILE_SDDL).unwrap();
        assert_ne!(
            transition.staging_security_sha256,
            transition.sealed_security_sha256
        );
        for hostile in [
            SEALED_FILE_DIFFERENT_OWNER_SDDL,
            SEALED_FILE_DIFFERENT_LABEL_SDDL,
        ] {
            assert_eq!(
                SecurityTransition::new(STAGING_FILE_SDDL, hostile)
                    .err()
                    .unwrap()
                    .code(),
                "authority_finalizer_seal_immutable_security_changed"
            );
        }
    }

    #[test]
    fn descriptor_transition_rejects_noop_unprotected_or_incomplete_policy() {
        assert_eq!(
            SecurityTransition::new(STAGING_FILE_SDDL, STAGING_FILE_SDDL)
                .err()
                .unwrap()
                .code(),
            "authority_finalizer_seal_transition_noop"
        );
        let unprotected = SEALED_FILE_SDDL.replace("D:P", "D:");
        assert_eq!(
            SecurityTransition::new(STAGING_FILE_SDDL, &unprotected)
                .err()
                .unwrap()
                .code(),
            "authority_finalizer_security_descriptor_incomplete"
        );
        let missing_label = SEALED_FILE_SDDL.replace("S:(ML;;NW;;;HI)", "");
        assert!(SecurityTransition::new(STAGING_FILE_SDDL, &missing_label).is_err());
    }

    #[test]
    fn stable_identity_rejects_files_without_unique_hash_bound_identity() {
        assert_eq!(
            PreSealStableIdentity::new_file(1, [1; 16], 2, 4, [2; 32])
                .unwrap_err()
                .code(),
            "authority_finalizer_seal_file_identity_invalid"
        );
        assert_eq!(
            PreSealStableIdentity::new_file(1, [1; 16], 1, 0, [2; 32])
                .unwrap_err()
                .code(),
            "authority_finalizer_seal_file_identity_invalid"
        );
        assert_eq!(
            PreSealStableIdentity::new_directory(0, [1; 16], 1, 0)
                .unwrap_err()
                .code(),
            "authority_finalizer_seal_identity_invalid"
        );
    }

    #[test]
    fn reopened_access_contract_rejects_every_mutating_right() {
        let read_only = READ_CONTROL
            | SYNCHRONIZE
            | FILE_READ_DATA
            | FILE_READ_ATTRIBUTES
            | FILE_READ_EA
            | ACCESS_SYSTEM_SECURITY;
        validate_read_only_access(read_only, FinalizerSealedObjectType::File).unwrap();
        let sealing = read_only | WRITE_DAC;
        validate_sealing_access(sealing, FinalizerSealedObjectType::File).unwrap();
        assert_eq!(read_only & MUTATING_FILE_ACCESS, 0);
        for denied in [
            DELETE,
            WRITE_OWNER,
            FILE_WRITE_DATA,
            FILE_APPEND_DATA,
            FILE_WRITE_EA,
            FILE_DELETE_CHILD,
            FILE_WRITE_ATTRIBUTES,
        ] {
            assert_eq!(
                validate_sealing_access(sealing | denied, FinalizerSealedObjectType::File)
                    .unwrap_err()
                    .code(),
                "authority_finalizer_sealing_handle_access_invalid"
            );
            assert_eq!(
                validate_read_only_access(read_only | denied, FinalizerSealedObjectType::File)
                    .unwrap_err()
                    .code(),
                "authority_finalizer_reopened_handle_not_read_only"
            );
        }
        assert!(
            validate_read_only_access(read_only | WRITE_DAC, FinalizerSealedObjectType::File)
                .is_err()
        );
        assert!(validate_sealing_access(
            sealing | GENERIC_ACCESS_MASK,
            FinalizerSealedObjectType::File
        )
        .is_err());
    }

    #[test]
    fn target_descriptor_exposes_non_null_explicit_dacl_and_label() {
        let descriptor = SecurityDescriptor::from_sddl(SEALED_FILE_SDDL).unwrap();
        let (dacl, label) = descriptor.acl_parts().unwrap();
        assert!(!dacl.is_null());
        assert!(!label.is_null());
    }

    #[test]
    fn ordinary_user_held_file_identity_and_bytes_are_exact() {
        assert_eq!(size_of::<PublicObjectBasicInformation>(), 56);
        let path = unique_temp_path("identity");
        let bytes = b"bounded-finalizer-seal-receipt";
        let mut file = OpenOptions::new()
            .create_new(true)
            .read(true)
            .write(true)
            .open(&path)
            .unwrap();
        file.write_all(bytes).unwrap();
        file.flush().unwrap();
        let handle = unsafe { OwnedHandle::from_raw_handle(file.into_raw_handle()) };
        let access = granted_access(&handle).unwrap();
        assert_eq!(access & FILE_READ_DATA, FILE_READ_DATA);
        assert_eq!(access & FILE_WRITE_DATA, FILE_WRITE_DATA);
        let identity = capture_preseal_identity(&handle, FinalizerSealedObjectType::File).unwrap();
        assert_eq!(identity.object_type(), FinalizerSealedObjectType::File);
        assert_eq!(identity.link_count(), 1);
        assert_eq!(identity.byte_length(), bytes.len() as u64);
        assert_eq!(identity.bytes_sha256(), Some(&sha256(bytes)));
        verify_identity_and_contents(&handle, &identity).unwrap();
        drop(handle);
        fs::remove_file(path).unwrap();
    }

    #[test]
    fn binary_hash_streams_beyond_small_object_limit_without_widening_state() {
        let path = unique_temp_path("large-binary");
        let bytes = (0..(MAX_SMALL_SEALABLE_FILE_BYTES as usize * 3))
            .map(|index| (index % 251) as u8)
            .collect::<Vec<_>>();
        fs::write(&path, &bytes).unwrap();

        let binary = OpenOptions::new().read(true).open(&path).unwrap();
        let binary = unsafe { OwnedHandle::from_raw_handle(binary.into_raw_handle()) };
        let identity =
            capture_preseal_identity_for_target(&binary, FinalizerSealTarget::BinaryFile).unwrap();
        let expected: [u8; 32] = Sha256::digest(&bytes).into();
        assert_eq!(identity.byte_length(), bytes.len() as u64);
        assert_eq!(identity.bytes_sha256(), Some(&expected));
        drop(binary);

        let state = OpenOptions::new().read(true).open(&path).unwrap();
        let state = unsafe { OwnedHandle::from_raw_handle(state.into_raw_handle()) };
        assert_eq!(
            capture_preseal_identity_for_target(&state, FinalizerSealTarget::ImmutableStateFile,)
                .unwrap_err()
                .code(),
            "authority_finalizer_seal_file_length_invalid"
        );
        drop(state);
        fs::remove_file(path).unwrap();
    }

    #[test]
    fn hostile_content_change_is_rejected_before_any_security_mutation() {
        let path = unique_temp_path("content-drift");
        fs::write(&path, b"first").unwrap();
        let first = OpenOptions::new()
            .read(true)
            .write(true)
            .open(&path)
            .unwrap();
        let handle = unsafe { OwnedHandle::from_raw_handle(first.into_raw_handle()) };
        let identity = capture_preseal_identity(&handle, FinalizerSealedObjectType::File).unwrap();
        fs::write(&path, b"other").unwrap();
        assert_eq!(
            verify_identity_and_contents(&handle, &identity)
                .unwrap_err()
                .code(),
            "authority_finalizer_seal_bytes_mismatch"
        );
        drop(handle);
        fs::remove_file(path).unwrap();
    }

    #[test]
    fn hostile_hardlink_change_is_rejected_before_any_security_mutation() {
        let path = unique_temp_path("link-source");
        let link = unique_temp_path("link-alias");
        fs::write(&path, b"stable").unwrap();
        let first = OpenOptions::new()
            .read(true)
            .write(true)
            .open(&path)
            .unwrap();
        let handle = unsafe { OwnedHandle::from_raw_handle(first.into_raw_handle()) };
        let identity = capture_preseal_identity(&handle, FinalizerSealedObjectType::File).unwrap();
        fs::hard_link(&path, &link).unwrap();
        assert_eq!(
            verify_identity_and_contents(&handle, &identity)
                .unwrap_err()
                .code(),
            "authority_finalizer_seal_identity_mismatch"
        );
        drop(handle);
        fs::remove_file(link).unwrap();
        fs::remove_file(path).unwrap();
    }

    #[test]
    fn privilege_restore_failure_overrides_success_and_operation_failure() {
        let operation_error = FinalizerSecurityError::new("operation-failed");
        let restore_error =
            FinalizerSecurityError::new("authority_finalizer_security_privilege_restore_failed");
        assert_eq!(
            complete_security_privilege_scope::<()>(Err(operation_error), Err(restore_error))
                .unwrap_err()
                .code(),
            "authority_finalizer_security_privilege_restore_failed"
        );
        assert_eq!(
            complete_security_privilege_scope::<()>(Ok(()), Err(restore_error))
                .unwrap_err()
                .code(),
            "authority_finalizer_security_privilege_restore_failed"
        );
        assert_eq!(
            complete_security_privilege_scope::<()>(Err(operation_error), Ok(()))
                .unwrap_err()
                .code(),
            "operation-failed"
        );
    }

    fn unique_temp_path(label: &str) -> PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        std::env::temp_dir().join(format!(
            "vrcforge-finalizer-security-{label}-{}-{nonce}.tmp",
            std::process::id()
        ))
    }
}

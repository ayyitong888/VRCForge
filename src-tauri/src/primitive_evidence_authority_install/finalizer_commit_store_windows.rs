//! Durable create-new storage for finalizer commit protocol receipts.
//!
//! Every file is a cumulative canonical protocol snapshot. A later snapshot
//! binds the exact filesystem identity and bytes of its predecessor, so restart
//! recovery cannot silently splice a different receipt chain. The store is not
//! connected to the production mutation path; it only supplies the durable
//! boundary and phase-aware recovery decision needed by that future adapter.

#![cfg_attr(not(test), allow(dead_code))]

#[cfg(test)]
use super::finalizer_generation_seal::generation_progress_relative_name;
use super::finalizer_security_windows::{
    transition_publication_security, with_finalizer_security_privilege,
    FinalizerPublicationSecurityPhase, FinalizerSecurityError,
};
#[cfg(test)]
use super::finalizer_security_windows::{
    FinalizerSealTarget, FinalizerSealedObjectType, FinalizerSecuritySealReceipt,
};
use super::{
    candidate_service_start_windows::RestrictedPrecommitStartAuthorization,
    finalizer_commit_protocol::{
        DurableFileIdentity, DurableReceiptWrite, FinalCommitPersistenceProjection,
        FinalizerCommitBinding, FinalizerCommitProtocolState, FinalizerCommitStage,
        ProtectedBlobNamespacePersistenceProjection, ProtocolWriteDisposition,
        RunnerPolicySealedIdentity, SealCompletePersistenceProjection,
        FINALIZER_COMMIT_STORE_SCHEMA,
    },
    finalizer_generation_seal::{
        enumerate_transaction_namespace_member_names, generation_progress_namespace_member,
        GenerationSealTerminalAuthorization,
    },
    receipt::HeldPayloadLease,
    receipt_windows::process_security,
    security_policy::{
        DIRECTORY_READ_EXECUTE_ACCESS, FINALIZER_COMMIT_RECEIPT_IMMUTABLE_SDDL,
        FINALIZER_COMMIT_TRANSACTION_PROGRESS_HANDLE_ACCESS,
        FINALIZER_COMMIT_TRANSACTION_READONLY_HANDLE_ACCESS,
        FINALIZER_COMMIT_TRANSACTION_RECEIPT_HANDLE_ACCESS, FINALIZER_COMMIT_TRANSACTION_ROOT_SDDL,
        FINALIZER_PUBLISHING_PRIVATE_SEALED_SDDL, GENERATION_SEALED_SDDL,
        LEDGER_FINAL_AUTHORITY_ACCESS, LEDGER_FINAL_SDDL, RUNTIME_BLOB_DIRECTORY_AUTHORITY_ACCESS,
        RUNTIME_BLOB_DIRECTORY_FINAL_SDDL, STABLE_ROOT_SDDL, STATE_IMMUTABLE_SDDL,
        STATE_STAGING_SDDL, WRITE_OWNER_ACCESS,
    },
    AuthorityMaintenanceError, VerifiedElevatedMaintenanceCapability, VerifiedMaintenanceLease,
};
use crate::primitive_evidence_authority_windows::AUTHORITY_PROTECTED_BLOB_NAMESPACE_DIRECTORY_NAME;
use serde::{Deserialize, Serialize};
use sha2::{Digest as _, Sha256};
use std::os::windows::ffi::OsStrExt;
use std::{
    collections::BTreeSet,
    mem::{size_of, zeroed},
    os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle, RawHandle},
    path::{Component, Path, PathBuf},
    ptr, slice,
};
#[cfg(test)]
use windows_sys::Win32::Security::{
    Authorization::SetSecurityInfo, PROTECTED_DACL_SECURITY_INFORMATION,
};
use windows_sys::Win32::Storage::FileSystem::{
    CreateFileW, ReOpenFile, FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT,
    OPEN_EXISTING,
};
use windows_sys::Win32::Storage::FileSystem::{
    FileCaseSensitiveInfo, GetDriveTypeW, GetFileInformationByHandleEx, GetFinalPathNameByHandleW,
    GetVolumePathNameW,
};
use windows_sys::{
    Wdk::{
        Foundation::{NtQueryObject, ObjectBasicInformation, OBJECT_ATTRIBUTES},
        Storage::FileSystem::{
            FileDispositionInformationEx, FileRenameInformation, NtCreateFile, NtFlushBuffersFile,
            NtSetInformationFile, FILE_CREATE, FILE_DIRECTORY_FILE, FILE_DISPOSITION_DELETE,
            FILE_DISPOSITION_INFORMATION_EX, FILE_NON_DIRECTORY_FILE, FILE_OPEN,
            FILE_OPEN_REPARSE_POINT, FILE_RENAME_INFORMATION, FILE_SYNCHRONOUS_IO_NONALERT,
            FILE_WRITE_THROUGH,
        },
    },
    Win32::{
        Foundation::{
            DuplicateHandle, LocalFree, DUPLICATE_SAME_ACCESS, FILETIME, INVALID_HANDLE_VALUE,
            UNICODE_STRING, WAIT_TIMEOUT,
        },
        Security::{
            Authorization::{
                ConvertSecurityDescriptorToStringSecurityDescriptorW,
                ConvertStringSecurityDescriptorToSecurityDescriptorW, GetSecurityInfo,
                SDDL_REVISION_1, SE_FILE_OBJECT,
            },
            GetSecurityDescriptorDacl, GetSecurityDescriptorSacl, ACL, DACL_SECURITY_INFORMATION,
            GROUP_SECURITY_INFORMATION, LABEL_SECURITY_INFORMATION, OWNER_SECURITY_INFORMATION,
            PSECURITY_DESCRIPTOR,
        },
        Storage::FileSystem::{
            GetFileInformationByHandle, ReadFile, SetFilePointerEx, WriteFile,
            BY_HANDLE_FILE_INFORMATION, DELETE, FILE_ADD_FILE, FILE_ATTRIBUTE_DIRECTORY,
            FILE_ATTRIBUTE_NORMAL, FILE_ATTRIBUTE_REPARSE_POINT, FILE_BEGIN, FILE_LIST_DIRECTORY,
            FILE_READ_ATTRIBUTES, FILE_READ_DATA, FILE_READ_EA, FILE_SHARE_DELETE, FILE_SHARE_READ,
            FILE_SHARE_WRITE, FILE_TRAVERSE, FILE_WRITE_DATA, READ_CONTROL, SYNCHRONIZE, WRITE_DAC,
        },
        System::{
            SystemServices::{ACCESS_SYSTEM_SECURITY, FILE_CS_FLAG_CASE_SENSITIVE_DIR},
            Threading::{
                GetCurrentProcess, GetCurrentProcessId, GetProcessId, GetProcessTimes,
                WaitForSingleObject,
            },
            IO::IO_STATUS_BLOCK,
        },
    },
};

#[cfg(test)]
const FINALIZER_SEAL_INTENT_SCHEMA: &str = "vrcforge.authority.finalizer-seal-intent.v1";
#[cfg(test)]
const FINALIZER_SEAL_PROGRESS_SCHEMA: &str = "vrcforge.authority.finalizer-seal-progress.v1";
#[cfg(test)]
const FINALIZER_SEAL_PROGRESS_READBACK_DOMAIN: &[u8] =
    b"vrcforge-authority-finalizer-seal-progress-readback-v1\0";
const PUBLISHED_RUNTIME_BINDING_PROJECTION_DOMAIN: &[u8] =
    b"vrcforge-published-runtime-binding-projection-v4\0";
const PROTECTED_BLOB_NAMESPACE_CANONICAL_PATH_DOMAIN: &[u8] =
    b"vrcforge-authority-protected-blob-namespace-canonical-path-v1\0";
const SEAL_INTENT_FILE_NAME: &str = "seal-intent.receipt.json";
const WORKER_SEAL_PROGRESS_FILE_NAME: &str = "worker-nonce-sealed.receipt.json";
const CANDIDATE_SEAL_PROGRESS_FILE_NAME: &str = "candidate-consumption-sealed.receipt.json";
const MAX_FINALIZER_COMMIT_RECEIPT_BYTES: usize = 64 * 1024;
const MAX_FINALIZER_IMAGE_BYTES: u64 = 512 * 1024 * 1024;
const PRIVATE_PUBLISHING_SUFFIX: &str = ".publishing";
#[cfg(test)]
const COMPLETE_SEAL_VERIFICATION_BITMAP: u16 = 0x03ff;
const FILE_OPENED_INFORMATION: usize = 1;
const FILE_CREATED_INFORMATION: usize = 2;
const STATUS_NO_SUCH_FILE: i32 = 0xc000_000fu32 as i32;
const STATUS_OBJECT_NAME_NOT_FOUND: i32 = 0xc000_0034u32 as i32;
const STATUS_OBJECT_NAME_COLLISION: i32 = 0xc000_0035u32 as i32;
const STATUS_OBJECT_PATH_NOT_FOUND: i32 = 0xc000_003au32 as i32;

#[repr(C)]
struct FileCaseSensitiveInformation {
    flags: u32,
}

const ROOT_ACCESS: u32 = FILE_LIST_DIRECTORY
    | FILE_TRAVERSE
    | FILE_READ_ATTRIBUTES
    | FILE_READ_EA
    | FILE_ADD_FILE
    | READ_CONTROL
    | SYNCHRONIZE;
const AUTHENTICATED_PARENT_ROOT_ACCESS: u32 =
    FILE_TRAVERSE | FILE_READ_ATTRIBUTES | READ_CONTROL | SYNCHRONIZE | ACCESS_SYSTEM_SECURITY;
pub(super) const AUTHENTICATED_TRANSACTION_ROOT_ACCESS: u32 =
    FINALIZER_COMMIT_TRANSACTION_RECEIPT_HANDLE_ACCESS;
const AUTHENTICATED_TRANSACTION_PROGRESS_ROOT_ACCESS: u32 =
    FINALIZER_COMMIT_TRANSACTION_PROGRESS_HANDLE_ACCESS;
const AUTHENTICATED_TRANSACTION_NAMESPACE_ROOT_ACCESS: u32 =
    FINALIZER_COMMIT_TRANSACTION_PROGRESS_HANDLE_ACCESS;
const RESTRICTED_PARENT_ROOT_ACCESS: u32 =
    FILE_TRAVERSE | FILE_READ_ATTRIBUTES | READ_CONTROL | SYNCHRONIZE;
const RESTRICTED_RUNTIME_GENERATION_ROOT_ACCESS: u32 = DIRECTORY_READ_EXECUTE_ACCESS;
const RESTRICTED_RUNTIME_LEDGER_ACCESS: u32 = LEDGER_FINAL_AUTHORITY_ACCESS;
const RESTRICTED_RUNTIME_LEDGER_SHARE_ACCESS: u32 = 0;
const RESTRICTED_RUNTIME_BLOB_DIRECTORY_ACCESS: u32 = RUNTIME_BLOB_DIRECTORY_AUTHORITY_ACCESS;
const RESTRICTED_RUNTIME_BLOB_DIRECTORY_SHARE_ACCESS: u32 = 0;
const MAX_RESTRICTED_RUNTIME_LEDGER_BYTES: u64 = 512 * 1024 * 1024;
const RESTRICTED_TRANSACTION_ROOT_ACCESS: u32 = FINALIZER_COMMIT_TRANSACTION_READONLY_HANDLE_ACCESS;
const RECEIPT_READ_ACCESS: u32 =
    FILE_READ_DATA | FILE_READ_ATTRIBUTES | FILE_READ_EA | READ_CONTROL | SYNCHRONIZE;
const RECEIPT_CREATE_ACCESS: u32 = RECEIPT_READ_ACCESS | FILE_WRITE_DATA | DELETE;
const AUTHENTICATED_RECEIPT_READ_ACCESS: u32 =
    FILE_READ_DATA | FILE_READ_ATTRIBUTES | READ_CONTROL | SYNCHRONIZE | ACCESS_SYSTEM_SECURITY;
const AUTHENTICATED_RECEIPT_CREATE_ACCESS: u32 =
    AUTHENTICATED_RECEIPT_READ_ACCESS | FILE_WRITE_DATA | DELETE | WRITE_DAC;
const AUTHENTICATED_RECEIPT_STAGING_RECOVERY_ACCESS: u32 =
    AUTHENTICATED_RECEIPT_READ_ACCESS | DELETE | WRITE_DAC;
const AUTHENTICATED_RECEIPT_SEALED_RECOVERY_ACCESS: u32 =
    AUTHENTICATED_RECEIPT_READ_ACCESS | DELETE | WRITE_DAC;
const RESTRICTED_RECEIPT_READ_ACCESS: u32 =
    FILE_READ_DATA | FILE_READ_ATTRIBUTES | FILE_READ_EA | READ_CONTROL | SYNCHRONIZE;
const FULL_SECURITY_INFORMATION: u32 = OWNER_SECURITY_INFORMATION
    | GROUP_SECURITY_INFORMATION
    | DACL_SECURITY_INFORMATION
    | LABEL_SECURITY_INFORMATION;
const MAX_CANONICAL_PATH_WORDS: usize = 32_768;
const DRIVE_FIXED: u32 = 3;

const STAGES: [FinalizerCommitStage; 6] = [
    FinalizerCommitStage::TransactionStarted,
    FinalizerCommitStage::ApplyReady,
    FinalizerCommitStage::SealReady,
    FinalizerCommitStage::ExitReady,
    FinalizerCommitStage::SealComplete,
    FinalizerCommitStage::FinalCommit,
];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum FinalizerArtifactSecurityPhase {
    Staging,
    SealInProgress,
    Sealed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum FinalizerArtifactDescriptorConstraint {
    StagingOnly,
    StagingOrSealed,
    SealedOnly,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct FinalizerArtifactSecurityExpectation {
    phase: FinalizerArtifactSecurityPhase,
    worker_nonce: FinalizerArtifactDescriptorConstraint,
    candidate_consumption: FinalizerArtifactDescriptorConstraint,
}

impl FinalizerArtifactSecurityExpectation {
    pub(super) fn phase(self) -> FinalizerArtifactSecurityPhase {
        self.phase
    }

    pub(super) fn worker_nonce(self) -> FinalizerArtifactDescriptorConstraint {
        self.worker_nonce
    }

    pub(super) fn candidate_consumption(self) -> FinalizerArtifactDescriptorConstraint {
        self.candidate_consumption
    }

    pub(super) fn require_observed_pair(
        self,
        worker_nonce: FinalizerArtifactSecurityPhase,
        candidate_consumption: FinalizerArtifactSecurityPhase,
    ) -> Result<(), AuthorityMaintenanceError> {
        if !constraint_accepts(self.worker_nonce, worker_nonce)
            || !constraint_accepts(self.candidate_consumption, candidate_consumption)
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_security_phase_mismatch",
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum FinalizerCommitRecoveryDirective {
    ResumeSystemApply,
    ResumeCandidateActivation,
    ResumeSystemExit,
    RecoverGenerationSealTerminalAuthorization,
    CompleteActiveHeadRuntimeAndPersistFinalCommit,
    ReadOnlyVerifyFinalCommitAndRuntime,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct PersistedReceiptFileReference {
    stage: FinalizerCommitStage,
    identity: DurableFileIdentity,
    receipt_sha256: [u8; 32],
    protocol_state_sha256: [u8; 32],
    security_readback_sha256: [u8; 32],
}

impl PersistedReceiptFileReference {
    fn new(
        stage: FinalizerCommitStage,
        identity: DurableFileIdentity,
        receipt_sha256: [u8; 32],
        protocol_state_sha256: [u8; 32],
        security_readback_sha256: [u8; 32],
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self {
            stage,
            identity,
            receipt_sha256,
            protocol_state_sha256,
            security_readback_sha256,
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        DurableFileIdentity::new(
            self.identity.volume_serial(),
            self.identity.file_id(),
            self.identity.link_count(),
            self.identity.byte_length(),
            self.identity.bytes_sha256(),
        )?;
        if is_zero(&self.receipt_sha256)
            || is_zero(&self.protocol_state_sha256)
            || is_zero(&self.security_readback_sha256)
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_file_reference_invalid",
            ));
        }
        Ok(())
    }

    pub(super) fn stage(self) -> FinalizerCommitStage {
        self.stage
    }

    pub(super) fn identity(self) -> DurableFileIdentity {
        self.identity
    }

    pub(super) fn receipt_sha256(self) -> [u8; 32] {
        self.receipt_sha256
    }

    pub(super) fn protocol_state_sha256(self) -> [u8; 32] {
        self.protocol_state_sha256
    }

    pub(super) fn security_readback_sha256(self) -> [u8; 32] {
        self.security_readback_sha256
    }
}

#[cfg(test)]
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct PersistedSealAuxiliaryReference {
    identity: DurableFileIdentity,
    security_readback_sha256: [u8; 32],
}

#[cfg(test)]
impl PersistedSealAuxiliaryReference {
    fn new(
        identity: DurableFileIdentity,
        security_readback_sha256: [u8; 32],
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self {
            identity,
            security_readback_sha256,
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        DurableFileIdentity::new(
            self.identity.volume_serial(),
            self.identity.file_id(),
            self.identity.link_count(),
            self.identity.byte_length(),
            self.identity.bytes_sha256(),
        )?;
        if is_zero(&self.security_readback_sha256) {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_seal_auxiliary_reference_invalid",
            ));
        }
        Ok(())
    }
}

#[cfg(test)]
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub(super) enum FinalizerSealArtifact {
    WorkerNonce,
    CandidateConsumption,
}

#[cfg(test)]
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct FinalizerSealIntentEvidence {
    worker_nonce: DurableFileIdentity,
    candidate_consumption: DurableFileIdentity,
    worker_staging_descriptor_sha256: [u8; 32],
    candidate_staging_descriptor_sha256: [u8; 32],
    sealed_descriptor_sha256: [u8; 32],
}

#[cfg(test)]
impl FinalizerSealIntentEvidence {
    pub(super) fn new(
        worker_nonce: DurableFileIdentity,
        candidate_consumption: DurableFileIdentity,
        worker_staging_descriptor_sha256: [u8; 32],
        candidate_staging_descriptor_sha256: [u8; 32],
        sealed_descriptor_sha256: [u8; 32],
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self {
            worker_nonce,
            candidate_consumption,
            worker_staging_descriptor_sha256,
            candidate_staging_descriptor_sha256,
            sealed_descriptor_sha256,
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        DurableFileIdentity::new(
            self.worker_nonce.volume_serial(),
            self.worker_nonce.file_id(),
            self.worker_nonce.link_count(),
            self.worker_nonce.byte_length(),
            self.worker_nonce.bytes_sha256(),
        )?;
        DurableFileIdentity::new(
            self.candidate_consumption.volume_serial(),
            self.candidate_consumption.file_id(),
            self.candidate_consumption.link_count(),
            self.candidate_consumption.byte_length(),
            self.candidate_consumption.bytes_sha256(),
        )?;
        if self.worker_nonce == self.candidate_consumption
            || is_zero(&self.worker_staging_descriptor_sha256)
            || is_zero(&self.candidate_staging_descriptor_sha256)
            || is_zero(&self.sealed_descriptor_sha256)
            || self.worker_staging_descriptor_sha256 == self.sealed_descriptor_sha256
            || self.candidate_staging_descriptor_sha256 == self.sealed_descriptor_sha256
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_seal_intent_invalid",
            ));
        }
        Ok(())
    }
}

#[cfg(test)]
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct FinalizerSealProgressEvidence {
    artifact: FinalizerSealArtifact,
    identity: DurableFileIdentity,
    sealed_descriptor_sha256: [u8; 32],
    exact_security_readback_sha256: [u8; 32],
    verification_bitmap: u16,
}

#[cfg(test)]
impl FinalizerSealProgressEvidence {
    /// Converts the native security module's held-handle receipt into the only
    /// production seal-progress representation accepted by this store. The ten
    /// bits cover target kind, file kind, unique link, volume, file id, length,
    /// content hash, exact sealed descriptor, writer close, and read-only
    /// reopen. Persisted/recovered evidence must contain that exact 10/10 set.
    pub(super) fn from_security_receipt(
        receipt: &FinalizerSecuritySealReceipt,
        expected_identity: DurableFileIdentity,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let artifact = match receipt.target() {
            FinalizerSealTarget::WorkerNonceFile => FinalizerSealArtifact::WorkerNonce,
            FinalizerSealTarget::CandidateConsumptionFile => {
                FinalizerSealArtifact::CandidateConsumption
            }
            _ => {
                return Err(AuthorityMaintenanceError(
                    "authority_finalizer_seal_progress_target_invalid",
                ));
            }
        };
        let identity = receipt.stable_identity();
        if identity.object_type() != FinalizerSealedObjectType::File
            || identity.link_count() != 1
            || identity.volume_serial() != expected_identity.volume_serial()
            || *identity.file_id() != expected_identity.file_id()
            || identity.byte_length() != expected_identity.byte_length()
            || identity.bytes_sha256().copied() != Some(expected_identity.bytes_sha256())
            || is_zero(receipt.sealed_security_sha256())
            || !receipt.write_handle_closed_before_reopen()
            || !receipt.read_only_reopen_verified()
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_seal_progress_native_receipt_invalid",
            ));
        }
        let sealed_descriptor_sha256 = *receipt.sealed_security_sha256();
        let exact_security_readback_sha256 = seal_progress_readback_digest(
            artifact,
            expected_identity,
            sealed_descriptor_sha256,
            COMPLETE_SEAL_VERIFICATION_BITMAP,
        );
        let value = Self {
            artifact,
            identity: expected_identity,
            sealed_descriptor_sha256,
            exact_security_readback_sha256,
            verification_bitmap: COMPLETE_SEAL_VERIFICATION_BITMAP,
        };
        value.validate()?;
        Ok(value)
    }

    #[cfg(test)]
    pub(super) fn new(
        artifact: FinalizerSealArtifact,
        identity: DurableFileIdentity,
        sealed_descriptor_sha256: [u8; 32],
    ) -> Result<Self, AuthorityMaintenanceError> {
        let exact_security_readback_sha256 = seal_progress_readback_digest(
            artifact,
            identity,
            sealed_descriptor_sha256,
            COMPLETE_SEAL_VERIFICATION_BITMAP,
        );
        let value = Self {
            artifact,
            identity,
            sealed_descriptor_sha256,
            exact_security_readback_sha256,
            verification_bitmap: COMPLETE_SEAL_VERIFICATION_BITMAP,
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), AuthorityMaintenanceError> {
        DurableFileIdentity::new(
            self.identity.volume_serial(),
            self.identity.file_id(),
            self.identity.link_count(),
            self.identity.byte_length(),
            self.identity.bytes_sha256(),
        )?;
        if is_zero(&self.sealed_descriptor_sha256)
            || self.verification_bitmap != COMPLETE_SEAL_VERIFICATION_BITMAP
            || self.exact_security_readback_sha256
                != seal_progress_readback_digest(
                    self.artifact,
                    self.identity,
                    self.sealed_descriptor_sha256,
                    self.verification_bitmap,
                )
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_seal_progress_invalid",
            ));
        }
        Ok(())
    }

    fn is_exact(self) -> bool {
        self.validate().is_ok()
    }
}

#[cfg(test)]
fn seal_progress_readback_digest(
    artifact: FinalizerSealArtifact,
    identity: DurableFileIdentity,
    sealed_descriptor_sha256: [u8; 32],
    verification_bitmap: u16,
) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(FINALIZER_SEAL_PROGRESS_READBACK_DOMAIN);
    digest.update([match artifact {
        FinalizerSealArtifact::WorkerNonce => 1,
        FinalizerSealArtifact::CandidateConsumption => 2,
    }]);
    digest.update(identity.volume_serial().to_be_bytes());
    digest.update(identity.file_id());
    digest.update(identity.byte_length().to_be_bytes());
    digest.update(identity.bytes_sha256());
    digest.update(sealed_descriptor_sha256);
    digest.update(verification_bitmap.to_be_bytes());
    digest.finalize().into()
}

#[cfg(test)]
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct DurableFinalizerSealIntent {
    schema: String,
    binding: FinalizerCommitBinding,
    authenticated_root_sha256: [u8; 32],
    exit_ready_file: PersistedReceiptFileReference,
    evidence: FinalizerSealIntentEvidence,
}

#[cfg(test)]
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct DurableFinalizerSealProgress {
    schema: String,
    binding: FinalizerCommitBinding,
    authenticated_root_sha256: [u8; 32],
    seal_intent_file: PersistedSealAuxiliaryReference,
    evidence: FinalizerSealProgressEvidence,
}

#[cfg(test)]
impl DurableFinalizerSealIntent {
    fn new(
        binding: FinalizerCommitBinding,
        authenticated_root_sha256: [u8; 32],
        exit_ready_file: PersistedReceiptFileReference,
        evidence: FinalizerSealIntentEvidence,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self {
            schema: FINALIZER_SEAL_INTENT_SCHEMA.to_string(),
            binding,
            authenticated_root_sha256,
            exit_ready_file,
            evidence,
        };
        value.validate(binding, authenticated_root_sha256, exit_ready_file)?;
        Ok(value)
    }

    fn parse_canonical(
        bytes: &[u8],
        binding: FinalizerCommitBinding,
        authenticated_root_sha256: [u8; 32],
        exit_ready_file: PersistedReceiptFileReference,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value: Self = serde_json::from_slice(bytes)
            .map_err(|_| AuthorityMaintenanceError("authority_finalizer_seal_intent_invalid"))?;
        value.validate(binding, authenticated_root_sha256, exit_ready_file)?;
        if serde_json::to_vec(&value)
            .map_err(|_| AuthorityMaintenanceError("authority_finalizer_seal_intent_invalid"))?
            != bytes
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_seal_intent_noncanonical",
            ));
        }
        Ok(value)
    }

    fn canonical_json(&self) -> Result<Vec<u8>, AuthorityMaintenanceError> {
        self.validate(
            self.binding,
            self.authenticated_root_sha256,
            self.exit_ready_file,
        )?;
        serde_json::to_vec(self)
            .map_err(|_| AuthorityMaintenanceError("authority_finalizer_seal_intent_invalid"))
    }

    fn validate(
        &self,
        binding: FinalizerCommitBinding,
        authenticated_root_sha256: [u8; 32],
        exit_ready_file: PersistedReceiptFileReference,
    ) -> Result<(), AuthorityMaintenanceError> {
        if self.schema != FINALIZER_SEAL_INTENT_SCHEMA
            || self.binding != binding
            || self.authenticated_root_sha256 != authenticated_root_sha256
            || is_zero(&self.authenticated_root_sha256)
            || self.exit_ready_file != exit_ready_file
            || self.exit_ready_file.stage() != FinalizerCommitStage::ExitReady
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_seal_intent_binding_mismatch",
            ));
        }
        self.exit_ready_file.validate()?;
        self.evidence.validate()
    }
}

#[cfg(test)]
impl DurableFinalizerSealProgress {
    fn new(
        binding: FinalizerCommitBinding,
        authenticated_root_sha256: [u8; 32],
        seal_intent_file: PersistedSealAuxiliaryReference,
        evidence: FinalizerSealProgressEvidence,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value = Self {
            schema: FINALIZER_SEAL_PROGRESS_SCHEMA.to_string(),
            binding,
            authenticated_root_sha256,
            seal_intent_file,
            evidence,
        };
        value.validate(binding, authenticated_root_sha256, seal_intent_file)?;
        Ok(value)
    }

    fn parse_canonical(
        bytes: &[u8],
        binding: FinalizerCommitBinding,
        authenticated_root_sha256: [u8; 32],
        seal_intent_file: PersistedSealAuxiliaryReference,
        expected_artifact: FinalizerSealArtifact,
        intent: &FinalizerSealIntentEvidence,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let value: Self = serde_json::from_slice(bytes)
            .map_err(|_| AuthorityMaintenanceError("authority_finalizer_seal_progress_invalid"))?;
        value.validate(binding, authenticated_root_sha256, seal_intent_file)?;
        value.validate_against_intent(expected_artifact, intent)?;
        if serde_json::to_vec(&value)
            .map_err(|_| AuthorityMaintenanceError("authority_finalizer_seal_progress_invalid"))?
            != bytes
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_seal_progress_noncanonical",
            ));
        }
        Ok(value)
    }

    fn canonical_json(&self) -> Result<Vec<u8>, AuthorityMaintenanceError> {
        self.validate(
            self.binding,
            self.authenticated_root_sha256,
            self.seal_intent_file,
        )?;
        serde_json::to_vec(self)
            .map_err(|_| AuthorityMaintenanceError("authority_finalizer_seal_progress_invalid"))
    }

    fn validate(
        &self,
        binding: FinalizerCommitBinding,
        authenticated_root_sha256: [u8; 32],
        seal_intent_file: PersistedSealAuxiliaryReference,
    ) -> Result<(), AuthorityMaintenanceError> {
        if self.schema != FINALIZER_SEAL_PROGRESS_SCHEMA
            || self.binding != binding
            || self.authenticated_root_sha256 != authenticated_root_sha256
            || is_zero(&self.authenticated_root_sha256)
            || self.seal_intent_file != seal_intent_file
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_seal_progress_binding_mismatch",
            ));
        }
        self.seal_intent_file.validate()?;
        self.evidence.validate()
    }

    fn validate_against_intent(
        &self,
        expected_artifact: FinalizerSealArtifact,
        intent: &FinalizerSealIntentEvidence,
    ) -> Result<(), AuthorityMaintenanceError> {
        let expected_identity = match expected_artifact {
            FinalizerSealArtifact::WorkerNonce => intent.worker_nonce,
            FinalizerSealArtifact::CandidateConsumption => intent.candidate_consumption,
        };
        if self.evidence.artifact != expected_artifact
            || self.evidence.identity != expected_identity
            || self.evidence.sealed_descriptor_sha256 != intent.sealed_descriptor_sha256
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_seal_progress_intent_mismatch",
            ));
        }
        Ok(())
    }
}

#[cfg(test)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
struct RecoveredSealProgress {
    intent: Option<PersistedSealAuxiliaryReference>,
    worker: Option<PersistedSealAuxiliaryReference>,
    candidate: Option<PersistedSealAuxiliaryReference>,
    worker_verified: bool,
    candidate_verified: bool,
}

#[cfg(test)]
impl RecoveredSealProgress {
    fn is_complete(self) -> bool {
        self.intent.is_some()
            && self.worker.is_some()
            && self.candidate.is_some()
            && self.worker_verified
            && self.candidate_verified
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct DurableFinalizerCommitEnvelope {
    schema: String,
    stage: FinalizerCommitStage,
    binding: FinalizerCommitBinding,
    authenticated_root_sha256: [u8; 32],
    receipt_sha256: [u8; 32],
    protocol_state_sha256: [u8; 32],
    previous_file: Option<PersistedReceiptFileReference>,
    // The protocol state is intentionally heap-backed. It is large enough that
    // keeping it inline while canonical validation recursively walks the typed
    // graph can exhaust the default Windows service/test thread stack. Box is
    // serde-transparent, so this does not change the canonical envelope bytes.
    protocol_state: Box<FinalizerCommitProtocolState>,
}

impl DurableFinalizerCommitEnvelope {
    fn new(
        state: &FinalizerCommitProtocolState,
        authenticated_root_sha256: [u8; 32],
        previous_file: Option<PersistedReceiptFileReference>,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let stage = state.latest_stage();
        let receipt_sha256 = state
            .receipt_sha256(stage)?
            .ok_or(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_receipt_missing",
            ))?;
        let value = Self {
            schema: FINALIZER_COMMIT_STORE_SCHEMA.to_string(),
            stage,
            binding: state.binding(),
            authenticated_root_sha256,
            receipt_sha256,
            protocol_state_sha256: state.state_sha256()?,
            previous_file,
            protocol_state: Box::new(state.clone()),
        };
        value.validate(
            state.binding(),
            authenticated_root_sha256,
            stage,
            previous_file.as_ref(),
        )?;
        Ok(value)
    }

    fn parse_canonical(
        bytes: &[u8],
        expected_binding: FinalizerCommitBinding,
        expected_authenticated_root_sha256: [u8; 32],
        expected_stage: FinalizerCommitStage,
        expected_previous: Option<&PersistedReceiptFileReference>,
    ) -> Result<Self, AuthorityMaintenanceError> {
        if bytes.is_empty() || bytes.len() > MAX_FINALIZER_COMMIT_RECEIPT_BYTES {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_receipt_size_invalid",
            ));
        }
        let value: Self = serde_json::from_slice(bytes).map_err(|_| {
            AuthorityMaintenanceError("authority_finalizer_commit_store_receipt_invalid")
        })?;
        value.validate(
            expected_binding,
            expected_authenticated_root_sha256,
            expected_stage,
            expected_previous,
        )?;
        if serde_json::to_vec(&value).map_err(|_| {
            AuthorityMaintenanceError("authority_finalizer_commit_store_canonical_json_invalid")
        })? != bytes
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_canonical_json_invalid",
            ));
        }
        Ok(value)
    }

    fn parse_transaction_started_self_authenticated(
        bytes: &[u8],
        expected_authenticated_root_sha256: [u8; 32],
        active_head_transaction_sha256: [u8; 32],
    ) -> Result<Self, AuthorityMaintenanceError> {
        if bytes.is_empty() || bytes.len() > MAX_FINALIZER_COMMIT_RECEIPT_BYTES {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_receipt_size_invalid",
            ));
        }
        if is_zero(&active_head_transaction_sha256) {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_active_head_transaction_invalid",
            ));
        }
        let value: Self = serde_json::from_slice(bytes).map_err(|_| {
            AuthorityMaintenanceError("authority_finalizer_commit_store_receipt_invalid")
        })?;
        let binding = value.protocol_state.binding();
        if binding.transaction_sha256() != active_head_transaction_sha256
            || binding.final_commit_store_root_identity_sha256()
                != expected_authenticated_root_sha256
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_active_head_binding_mismatch",
            ));
        }
        value.validate(
            binding,
            expected_authenticated_root_sha256,
            FinalizerCommitStage::TransactionStarted,
            None,
        )?;
        if serde_json::to_vec(&value).map_err(|_| {
            AuthorityMaintenanceError("authority_finalizer_commit_store_canonical_json_invalid")
        })? != bytes
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_canonical_json_invalid",
            ));
        }
        Ok(value)
    }

    fn canonical_json(&self) -> Result<Vec<u8>, AuthorityMaintenanceError> {
        self.validate(
            self.binding,
            self.authenticated_root_sha256,
            self.stage,
            self.previous_file.as_ref(),
        )?;
        serde_json::to_vec(self).map_err(|_| {
            AuthorityMaintenanceError("authority_finalizer_commit_store_canonical_json_invalid")
        })
    }

    fn validate(
        &self,
        expected_binding: FinalizerCommitBinding,
        expected_authenticated_root_sha256: [u8; 32],
        expected_stage: FinalizerCommitStage,
        expected_previous: Option<&PersistedReceiptFileReference>,
    ) -> Result<(), AuthorityMaintenanceError> {
        if self.schema != FINALIZER_COMMIT_STORE_SCHEMA
            || self.stage != expected_stage
            || self.binding != expected_binding
            || self.authenticated_root_sha256 != expected_authenticated_root_sha256
            || is_zero(&self.authenticated_root_sha256)
            || self.protocol_state.binding() != expected_binding
            || self.protocol_state.latest_stage() != expected_stage
            || self.previous_file.as_ref() != expected_previous
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_binding_mismatch",
            ));
        }
        match (
            stage_predecessor(expected_stage),
            self.previous_file.as_ref(),
        ) {
            (None, None) => {}
            (Some(predecessor), Some(previous)) if previous.stage == predecessor => {
                previous.validate()?;
            }
            _ => {
                return Err(AuthorityMaintenanceError(
                    "authority_finalizer_commit_store_chain_gap",
                ));
            }
        }
        self.protocol_state.to_canonical_json()?;
        // The typed state validates before serialization, while the enclosing
        // envelope parser separately requires byte-for-byte canonical JSON.
        // Re-deserializing this already-typed state here adds no invariant and
        // needlessly stacks a second large protocol value on service threads.
        if self.protocol_state.state_sha256()? != self.protocol_state_sha256
            || self.protocol_state.receipt_sha256(expected_stage)?.ok_or(
                AuthorityMaintenanceError("authority_finalizer_commit_store_receipt_missing"),
            )? != self.receipt_sha256
            || is_zero(&self.receipt_sha256)
            || is_zero(&self.protocol_state_sha256)
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_receipt_mismatch",
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct PersistedFinalizerCommitStage {
    disposition: ProtocolWriteDisposition,
    file: PersistedReceiptFileReference,
}

impl PersistedFinalizerCommitStage {
    pub(super) fn disposition(self) -> ProtocolWriteDisposition {
        self.disposition
    }

    pub(super) fn file(self) -> PersistedReceiptFileReference {
        self.file
    }
}

/// Store-local capability for the SealComplete publication edge. Production
/// callers can obtain it only by presenting the generation sealer's terminal
/// authorization; the persisted protocol projection must still compare equal
/// immediately before the create-new receipt is published.
pub(super) struct SealCompletePersistenceAuthorization {
    projection: SealCompletePersistenceProjection,
}

impl SealCompletePersistenceAuthorization {
    pub(super) fn from_generation_seal_authorization(
        authorization: &GenerationSealTerminalAuthorization,
    ) -> Result<Self, AuthorityMaintenanceError> {
        Ok(Self {
            projection: SealCompletePersistenceProjection::from_authorization(authorization)?,
        })
    }

    fn validate(
        &self,
        binding: FinalizerCommitBinding,
        state: &FinalizerCommitProtocolState,
    ) -> Result<(), AuthorityMaintenanceError> {
        let state_projection =
            state
                .seal_complete_persistence_projection()?
                .ok_or(AuthorityMaintenanceError(
                    "authority_finalizer_seal_complete_persistence_projection_missing",
                ))?;
        if state.binding() != binding || self.projection != state_projection {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_seal_complete_authorization_mismatch",
            ));
        }
        Ok(())
    }
}

/// Held process-epoch capability for terminal commit publication. Production
/// construction duplicates the already-verified bootstrap process handle and
/// reopens its retained image handle; it never accepts caller-supplied process
/// or image digests. The lease is bound to one authenticated store, protocol
/// binding, and durable ExitReady receipt.
#[must_use = "the elevated finalizer lease must guard terminal receipt publication"]
pub(super) struct NativeElevatedFinalizerCommitLease {
    binding: FinalizerCommitBinding,
    authenticated_root_sha256: [u8; 32],
    exit_ready_tip: PersistedReceiptFileReference,
    process_id: u32,
    process_creation_time: u64,
    session_id: u32,
    image_identity: NativeFileIdentity,
    image_sha256: [u8; 32],
    bootstrap_binding_sha256: [u8; 32],
    payload_set_binding_sha256: [u8; 32],
    backend: NativeElevatedFinalizerCommitLeaseBackend,
}

enum NativeElevatedFinalizerCommitLeaseBackend {
    Native {
        process: OwnedHandle,
        image: OwnedHandle,
    },
    #[cfg(test)]
    Fixture { actor_epoch: u64 },
}

/// One-use capability for the irreversible FinalCommit store edge. There is
/// no generic production constructor: the native adapter must present the
/// held current-process lease plus typed active-head, dormant committed-
/// runtime, pipe absence, writer-roster, activation-gate, and operation-
/// scoped zero-residue readbacks already embedded in protocol state.
pub(super) struct FinalCommitPersistenceAuthorization {
    binding: FinalizerCommitBinding,
    authenticated_root_sha256: [u8; 32],
    final_commit_gate_sha256: [u8; 32],
    seal_complete_tip: PersistedReceiptFileReference,
    projection: FinalCommitPersistenceProjection,
}

impl FinalCommitPersistenceAuthorization {
    fn from_elevated_finalizer_lease(
        store: &FinalizerCommitReceiptStore,
        state: &FinalizerCommitProtocolState,
        seal_complete_tip: PersistedReceiptFileReference,
        lease: &NativeElevatedFinalizerCommitLease,
        expected_durable_stage: FinalizerCommitStage,
    ) -> Result<Self, AuthorityMaintenanceError> {
        lease.revalidate(store, expected_durable_stage)?;
        let projection =
            state
                .final_commit_persistence_projection()?
                .ok_or(AuthorityMaintenanceError(
                    "authority_finalizer_final_commit_persistence_projection_missing",
                ))?;
        let value = Self {
            binding: store.binding,
            authenticated_root_sha256: store.authenticated_root_sha256,
            final_commit_gate_sha256: store.final_commit_gate_sha256,
            seal_complete_tip,
            projection,
        };
        value.validate(
            store.binding,
            store.authenticated_root_sha256,
            store.final_commit_gate_sha256,
            seal_complete_tip,
            state,
        )?;
        Ok(value)
    }

    fn validate(
        &self,
        binding: FinalizerCommitBinding,
        authenticated_root_sha256: [u8; 32],
        final_commit_gate_sha256: [u8; 32],
        seal_complete_tip: PersistedReceiptFileReference,
        state: &FinalizerCommitProtocolState,
    ) -> Result<(), AuthorityMaintenanceError> {
        let projection =
            state
                .final_commit_persistence_projection()?
                .ok_or(AuthorityMaintenanceError(
                    "authority_finalizer_final_commit_persistence_projection_missing",
                ))?;
        if self.binding != binding
            || self.authenticated_root_sha256 != authenticated_root_sha256
            || is_zero(&self.authenticated_root_sha256)
            || self.final_commit_gate_sha256 != final_commit_gate_sha256
            || self.final_commit_gate_sha256 != binding.expected_final_commit_gate_sha256()
            || self.seal_complete_tip != seal_complete_tip
            || self.seal_complete_tip.stage() != FinalizerCommitStage::SealComplete
            || self.projection != projection
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_final_commit_authorization_mismatch",
            ));
        }
        self.seal_complete_tip.validate()?;
        Ok(())
    }

    #[cfg(test)]
    fn for_test(
        binding: FinalizerCommitBinding,
        authenticated_root_sha256: [u8; 32],
        final_commit_gate_sha256: [u8; 32],
        seal_complete_tip: PersistedReceiptFileReference,
        state: &FinalizerCommitProtocolState,
    ) -> Result<Self, AuthorityMaintenanceError> {
        Ok(Self {
            binding,
            authenticated_root_sha256,
            final_commit_gate_sha256,
            seal_complete_tip,
            projection: state.final_commit_persistence_projection()?.ok_or(
                AuthorityMaintenanceError(
                    "authority_finalizer_final_commit_persistence_projection_missing",
                ),
            )?,
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct RecoveredFinalizerCommitState {
    protocol_state: FinalizerCommitProtocolState,
    files: Vec<PersistedReceiptFileReference>,
    security: FinalizerArtifactSecurityExpectation,
    directive: FinalizerCommitRecoveryDirective,
}

impl RecoveredFinalizerCommitState {
    pub(super) fn protocol_state(&self) -> &FinalizerCommitProtocolState {
        &self.protocol_state
    }

    pub(super) fn files(&self) -> &[PersistedReceiptFileReference] {
        &self.files
    }

    pub(super) fn tip(&self) -> PersistedReceiptFileReference {
        *self.files.last().expect("recovered state has a receipt")
    }

    pub(super) fn security(&self) -> FinalizerArtifactSecurityExpectation {
        self.security
    }

    pub(super) fn directive(&self) -> FinalizerCommitRecoveryDirective {
        self.directive
    }
}

/// Terminal-only readback returned by the active-head recovery factory. The
/// wrapper cannot be constructed from a caller-supplied binding and therefore
/// represents an exact FinalCommit chain recovered from the authenticated
/// transaction namespace.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct RecoveredFinalCommitReadback {
    active_head_transaction_sha256: [u8; 32],
    authenticated_root_sha256: [u8; 32],
    tip: PersistedReceiptFileReference,
    projection: FinalCommitPersistenceProjection,
}

impl RecoveredFinalCommitReadback {
    pub(super) fn active_head_transaction_sha256(self) -> [u8; 32] {
        self.active_head_transaction_sha256
    }

    pub(super) fn authenticated_root_sha256(self) -> [u8; 32] {
        self.authenticated_root_sha256
    }

    pub(super) fn tip(self) -> PersistedReceiptFileReference {
        self.tip
    }

    pub(super) fn projection(self) -> FinalCommitPersistenceProjection {
        self.projection
    }
}

pub(super) struct FinalizerCommitReceiptStore {
    root: OwnedHandle,
    namespace_root: OwnedHandle,
    root_identity: NativeFileIdentity,
    namespace_root_readback: Option<AuthenticatedFinalizerCommitRootReadback>,
    root_canonical_path: String,
    authenticated_root_sha256: [u8; 32],
    final_commit_gate_sha256: [u8; 32],
    root_reverify: Option<RootReverify>,
    namespace_root_reverify: Option<RootReverify>,
    receipt_reverify: Option<ReceiptReverify>,
    binding: FinalizerCommitBinding,
}

type RootReverify =
    fn(&OwnedHandle) -> Result<AuthenticatedFinalizerCommitRootReadback, AuthorityMaintenanceError>;
type ReceiptReverify = fn(
    &OwnedHandle,
    FinalizerCommitReceiptHandleKind,
    &str,
) -> Result<[u8; 32], AuthorityMaintenanceError>;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct AuthenticatedFinalizerCommitRootReadback {
    identity: NativeFileIdentity,
    canonical_path_readback_sha256: [u8; 32],
    complete_security_readback_sha256: [u8; 32],
    granted_access_readback_sha256: [u8; 32],
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum FinalizerCommitRootHandleKind {
    ParentNamespace,
    TransactionRoot,
    TransactionProgressRoot,
    TransactionNamespaceRoot,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum RestrictedFinalizerCommitRootHandleKind {
    StateRoot,
    ParentNamespace,
    TransactionRoot,
    GenerationNamespace,
    SealedGeneration,
    RuntimeBlobDirectory,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum FinalizerCommitReceiptHandleKind {
    PublishingInspectionStaging,
    PublishingInspectionSealed,
    PublishingCreate,
    PublishingStagingRecovery,
    PublishingCreateSealed,
    PublishingStagingRecoverySealed,
    PublishingRecoverySealed,
    PublishedTightening,
    PublishedReadOnly,
    RestrictedPublishedReadOnly,
}

/// Already-open canonical parent namespace. Authentication never opens this
/// object by path, and transaction roots can only be reached relative to this
/// held directory handle using the exact transaction digest as their name.
pub(super) struct AuthenticatedFinalizerCommitsParentRoot {
    root: OwnedHandle,
    readback: AuthenticatedFinalizerCommitRootReadback,
    canonical_path: String,
}

impl AuthenticatedFinalizerCommitsParentRoot {
    pub(super) fn authenticate_held(
        root: OwnedHandle,
        expected_canonical_path: &Path,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let expected = normalize_expected_canonical_path(expected_canonical_path)?;
        let readback =
            authenticate_commit_root_handle(&root, FinalizerCommitRootHandleKind::ParentNamespace)?;
        if readback.canonical_path_readback_sha256 != canonical_path_sha256(&expected) {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_parent_path_mismatch",
            ));
        }
        Ok(Self {
            root,
            readback,
            canonical_path: expected,
        })
    }

    pub(super) fn open_transaction_child(
        &self,
        transaction_sha256: [u8; 32],
    ) -> Result<AuthenticatedFinalizerCommitStoreRoot, AuthorityMaintenanceError> {
        if is_zero(&transaction_sha256) {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_transaction_name_invalid",
            ));
        }
        self.reverify()?;
        let transaction_name = hex_lower(&transaction_sha256);
        let root = nt_open_relative_directory(
            &self.root,
            &transaction_name,
            AUTHENTICATED_TRANSACTION_ROOT_ACCESS,
        )?
        .ok_or(AuthorityMaintenanceError(
            "authority_finalizer_commit_transaction_root_missing",
        ))?;
        let readback =
            authenticate_commit_root_handle(&root, FinalizerCommitRootHandleKind::TransactionRoot)?;
        if readback.identity.volume_serial != self.readback.identity.volume_serial {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_transaction_volume_mismatch",
            ));
        }
        let expected_path = format!("{}\\{transaction_name}", self.canonical_path);
        if readback.canonical_path_readback_sha256 != canonical_path_sha256(&expected_path) {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_transaction_path_mismatch",
            ));
        }
        let progress_root = nt_open_relative_directory(
            &self.root,
            &transaction_name,
            AUTHENTICATED_TRANSACTION_PROGRESS_ROOT_ACCESS,
        )?
        .ok_or(AuthorityMaintenanceError(
            "authority_finalizer_commit_transaction_progress_root_missing",
        ))?;
        let progress_readback = authenticate_commit_root_handle(
            &progress_root,
            FinalizerCommitRootHandleKind::TransactionProgressRoot,
        )?;
        validate_shared_transaction_root_readbacks(
            &readback,
            &progress_readback,
            canonical_path_sha256(&expected_path),
        )?;
        let namespace_root = nt_open_relative_directory(
            &self.root,
            &transaction_name,
            AUTHENTICATED_TRANSACTION_NAMESPACE_ROOT_ACCESS,
        )?
        .ok_or(AuthorityMaintenanceError(
            "authority_finalizer_commit_transaction_namespace_root_missing",
        ))?;
        let namespace_readback = authenticate_commit_root_handle(
            &namespace_root,
            FinalizerCommitRootHandleKind::TransactionNamespaceRoot,
        )?;
        validate_shared_transaction_root_readbacks(
            &readback,
            &namespace_readback,
            canonical_path_sha256(&expected_path),
        )?;
        let final_commit_store_root_identity_sha256 = authenticated_root_digest(&readback);
        self.reverify()?;
        Ok(AuthenticatedFinalizerCommitStoreRoot {
            root,
            readback,
            namespace_root,
            namespace_readback,
            canonical_path: expected_path,
            root_reverify: authenticate_transaction_root_reverify,
            namespace_root_reverify: authenticate_transaction_namespace_root_reverify,
            receipt_reverify: authenticate_receipt_handle,
            generation_progress_root: Some(AuthenticatedFinalizerGenerationProgressRoot {
                root: progress_root,
                canonical_path: format!("{}\\{transaction_name}", self.canonical_path),
                final_commit_store_root_identity_sha256,
            }),
        })
    }

    #[cfg(test)]
    pub(super) fn repair_and_recover_active_head_final_commit(
        &self,
        active_head_transaction_sha256: [u8; 32],
    ) -> Result<RecoveredFinalCommitReadback, AuthorityMaintenanceError> {
        let root = self.open_transaction_child(active_head_transaction_sha256)?;
        let store = FinalizerCommitReceiptStore::from_self_authenticated_root(
            root,
            active_head_transaction_sha256,
        )?;
        store.recover_typed_final_commit(active_head_transaction_sha256)
    }

    fn reverify(&self) -> Result<(), AuthorityMaintenanceError> {
        let current = authenticate_commit_root_handle(
            &self.root,
            FinalizerCommitRootHandleKind::ParentNamespace,
        )?;
        if !same_authenticated_root_readback(&current, &self.readback) {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_parent_authentication_drift",
            ));
        }
        Ok(())
    }
}

/// Restricted-service view rooted in an already-held layout state directory.
/// The finalizer namespace is opened only relative to that handle; neither the
/// constructor nor either verification path can repair a receipt or request a
/// security privilege.
pub(super) struct RestrictedFinalizerCommitsParentRoot {
    state_root: OwnedHandle,
    state_readback: AuthenticatedFinalizerCommitRootReadback,
    root: OwnedHandle,
    readback: AuthenticatedFinalizerCommitRootReadback,
    state_canonical_path: String,
    canonical_path: String,
}

impl RestrictedFinalizerCommitsParentRoot {
    /// Opens the one installed state root with the exact restricted-service
    /// access mask, then keeps that authenticated handle as the provenance
    /// anchor for every relative child open performed by this capability.
    pub(super) fn open_installed(
        layout: &super::AuthorityLayout,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let installed = super::AuthorityLayout::installed()
            .map_err(|_| AuthorityMaintenanceError("authority_finalizer_commit_layout_invalid"))?;
        if layout != &installed {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_layout_not_installed",
            ));
        }
        let state_root =
            open_absolute_directory_exact(installed.state_root(), RESTRICTED_PARENT_ROOT_ACCESS)?;
        Self::authenticate_layout_state_root_held(state_root, &installed)
    }

    pub(super) fn authenticate_layout_state_root_held(
        state_root: OwnedHandle,
        layout: &super::AuthorityLayout,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let expected_state = normalize_expected_layout_path(layout.state_root())?;
        let expected_parent = normalize_expected_canonical_path(&layout.finalizer_commits_root())?;
        if expected_parent != format!("{expected_state}\\finalizer-commits") {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_layout_parent_path_mismatch",
            ));
        }
        let state_readback = authenticate_restricted_commit_root_handle(
            &state_root,
            RestrictedFinalizerCommitRootHandleKind::StateRoot,
        )?;
        if state_readback.canonical_path_readback_sha256 != canonical_path_sha256(&expected_state) {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_layout_state_path_mismatch",
            ));
        }
        let root = nt_open_relative_directory(
            &state_root,
            "finalizer-commits",
            RESTRICTED_PARENT_ROOT_ACCESS,
        )?
        .ok_or(AuthorityMaintenanceError(
            "authority_finalizer_commit_parent_root_missing",
        ))?;
        let readback = authenticate_restricted_commit_root_handle(
            &root,
            RestrictedFinalizerCommitRootHandleKind::ParentNamespace,
        )?;
        if readback.identity.volume_serial != state_readback.identity.volume_serial
            || readback.canonical_path_readback_sha256 != canonical_path_sha256(&expected_parent)
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_layout_parent_provenance_mismatch",
            ));
        }
        let value = Self {
            state_root,
            state_readback,
            root,
            readback,
            state_canonical_path: expected_state,
            canonical_path: expected_parent,
        };
        value.reverify()?;
        Ok(value)
    }

    pub(super) fn verify_published_final_commit(
        self,
    ) -> Result<VerifiedPublishedFinalCommitLease, AuthorityMaintenanceError> {
        let active_head = RestrictedActiveHeadLease::open(&self)?;
        let active_head_transaction_sha256 = active_head.value.transaction_sha256()?;
        let transaction = self.open_transaction_child(active_head_transaction_sha256)?;
        let chain = transaction.strict_scan_exact(
            active_head_transaction_sha256,
            FinalizerCommitStage::FinalCommit,
        )?;
        let readback = recovered_final_commit_readback(
            &chain,
            active_head_transaction_sha256,
            transaction.authenticated_root_sha256(),
        )?;
        validate_final_commit_against_active_head(&readback, &active_head.value)?;
        Ok(VerifiedPublishedFinalCommitLease {
            parent: self,
            active_head,
            transaction,
            chain,
            readback,
        })
    }

    pub(super) fn verify_seal_complete_precommit(
        self,
        authorization: RestrictedPrecommitStartAuthorization,
    ) -> Result<VerifiedSealCompletePrecommitLease, AuthorityMaintenanceError> {
        let (locator, credential_binding) = authorization.into_parts()?;
        let active_head_transaction_sha256 = locator.transaction_sha256();
        let transaction = self.open_transaction_child(active_head_transaction_sha256)?;
        let chain = transaction.strict_scan_exact(
            active_head_transaction_sha256,
            FinalizerCommitStage::SealComplete,
        )?;
        let readback = recovered_seal_complete_readback(
            &chain,
            active_head_transaction_sha256,
            transaction.authenticated_root_sha256(),
        )?;
        validate_precommit_against_candidate_binding(&readback, credential_binding)?;
        Ok(VerifiedSealCompletePrecommitLease {
            parent: self,
            transaction,
            chain,
            readback,
        })
    }

    fn open_transaction_child(
        &self,
        transaction_sha256: [u8; 32],
    ) -> Result<RestrictedFinalizerCommitTransactionRoot, AuthorityMaintenanceError> {
        if is_zero(&transaction_sha256) {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_transaction_name_invalid",
            ));
        }
        self.reverify()?;
        let transaction_name = hex_lower(&transaction_sha256);
        let expected_path = format!("{}\\{transaction_name}", self.canonical_path);
        let root = nt_open_relative_directory(
            &self.root,
            &transaction_name,
            RESTRICTED_TRANSACTION_ROOT_ACCESS,
        )?
        .ok_or(AuthorityMaintenanceError(
            "authority_finalizer_commit_transaction_root_missing",
        ))?;
        let readback = authenticate_restricted_commit_root_handle(
            &root,
            RestrictedFinalizerCommitRootHandleKind::TransactionRoot,
        )?;
        let namespace_root = nt_open_relative_directory(
            &self.root,
            &transaction_name,
            RESTRICTED_TRANSACTION_ROOT_ACCESS,
        )?
        .ok_or(AuthorityMaintenanceError(
            "authority_finalizer_commit_transaction_namespace_root_missing",
        ))?;
        let namespace_readback = authenticate_restricted_commit_root_handle(
            &namespace_root,
            RestrictedFinalizerCommitRootHandleKind::TransactionRoot,
        )?;
        validate_shared_transaction_root_readbacks(
            &readback,
            &namespace_readback,
            canonical_path_sha256(&expected_path),
        )?;
        if readback.identity.volume_serial != self.readback.identity.volume_serial {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_transaction_volume_mismatch",
            ));
        }
        self.reverify()?;
        let value = RestrictedFinalizerCommitTransactionRoot {
            authenticated_root_sha256: authenticated_root_digest(&readback),
            root,
            readback,
            namespace_root,
            namespace_readback,
            canonical_path: expected_path,
            root_reverify: Some(authenticate_restricted_transaction_root_reverify),
            receipt_reverify: Some(authenticate_restricted_receipt_handle),
            receipt_access: RESTRICTED_RECEIPT_READ_ACCESS,
        };
        value.verify_unchanged()?;
        Ok(value)
    }

    fn reverify(&self) -> Result<(), AuthorityMaintenanceError> {
        let state_current = authenticate_restricted_commit_root_handle(
            &self.state_root,
            RestrictedFinalizerCommitRootHandleKind::StateRoot,
        )?;
        let parent_current = authenticate_restricted_commit_root_handle(
            &self.root,
            RestrictedFinalizerCommitRootHandleKind::ParentNamespace,
        )?;
        if !same_authenticated_root_readback(&state_current, &self.state_readback)
            || !same_authenticated_root_readback(&parent_current, &self.readback)
            || state_current.canonical_path_readback_sha256
                != canonical_path_sha256(&self.state_canonical_path)
            || parent_current.canonical_path_readback_sha256
                != canonical_path_sha256(&self.canonical_path)
            || state_current.identity.volume_serial != parent_current.identity.volume_serial
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_restricted_parent_authentication_drift",
            ));
        }
        Ok(())
    }
}

struct RestrictedFinalizerCommitTransactionRoot {
    authenticated_root_sha256: [u8; 32],
    root: OwnedHandle,
    readback: AuthenticatedFinalizerCommitRootReadback,
    namespace_root: OwnedHandle,
    namespace_readback: AuthenticatedFinalizerCommitRootReadback,
    canonical_path: String,
    root_reverify: Option<RootReverify>,
    receipt_reverify: Option<ReceiptReverify>,
    receipt_access: u32,
}

impl RestrictedFinalizerCommitTransactionRoot {
    fn authenticated_root_sha256(&self) -> [u8; 32] {
        self.authenticated_root_sha256
    }

    fn strict_scan_exact(
        &self,
        active_head_transaction_sha256: [u8; 32],
        expected_terminal_stage: FinalizerCommitStage,
    ) -> Result<StrictPublishedReceiptChain, AuthorityMaintenanceError> {
        strict_scan_published_receipt_chain(
            self,
            active_head_transaction_sha256,
            expected_terminal_stage,
        )
    }

    fn verify_unchanged(&self) -> Result<(), AuthorityMaintenanceError> {
        let current_identity = native_file_identity(&self.root)?;
        let namespace_identity = native_file_identity(&self.namespace_root)?;
        if !same_root_identity(&current_identity, &self.readback.identity)
            || !same_root_identity(&namespace_identity, &self.namespace_readback.identity)
            || !same_root_identity(&current_identity, &namespace_identity)
            || canonical_path_sha256(&canonical_handle_path(&self.root)?)
                != self.readback.canonical_path_readback_sha256
            || canonical_path_sha256(&canonical_handle_path(&self.namespace_root)?)
                != self.namespace_readback.canonical_path_readback_sha256
            || self.readback.canonical_path_readback_sha256
                != canonical_path_sha256(&self.canonical_path)
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_restricted_transaction_identity_drift",
            ));
        }
        if let Some(reverify) = self.root_reverify {
            let root_current = reverify(&self.root)?;
            let namespace_current = reverify(&self.namespace_root)?;
            if !same_authenticated_root_readback(&root_current, &self.readback)
                || !same_authenticated_root_readback(&namespace_current, &self.namespace_readback)
            {
                return Err(AuthorityMaintenanceError(
                    "authority_finalizer_commit_restricted_transaction_authentication_drift",
                ));
            }
        }
        Ok(())
    }

    #[cfg(test)]
    fn open_unsecured_test(root_path: &Path) -> Result<Self, AuthorityMaintenanceError> {
        let root = open_root_for_test(root_path)?;
        let namespace_root = open_root_for_test(root_path)?;
        let readback =
            unsecured_test_root_readback(&root, FinalizerCommitRootHandleKind::TransactionRoot)?;
        let namespace_readback = unsecured_test_root_readback(
            &namespace_root,
            FinalizerCommitRootHandleKind::TransactionNamespaceRoot,
        )?;
        let canonical_path = canonical_handle_path(&root)?;
        let authenticated_root_sha256 = test_root_digest(&readback.identity);
        Ok(Self {
            authenticated_root_sha256,
            root,
            readback,
            namespace_root,
            namespace_readback,
            canonical_path,
            root_reverify: None,
            receipt_reverify: None,
            receipt_access: RECEIPT_READ_ACCESS,
        })
    }
}

struct RestrictedActiveHeadLease {
    activations_root: OwnedHandle,
    activations_readback: AuthenticatedFinalizerCommitRootReadback,
    activations_canonical_path: String,
    file: OwnedHandle,
    file_identity: NativeFileIdentity,
    file_bytes: Vec<u8>,
    file_capability_sha256: [u8; 32],
    file_canonical_path: String,
    value: super::ProtectedActiveHead,
}

impl RestrictedActiveHeadLease {
    fn open(
        parent: &RestrictedFinalizerCommitsParentRoot,
    ) -> Result<Self, AuthorityMaintenanceError> {
        parent.reverify()?;
        let activations_canonical_path = format!("{}\\activations", parent.state_canonical_path);
        let activations_root = nt_open_relative_directory(
            &parent.state_root,
            "activations",
            RESTRICTED_PARENT_ROOT_ACCESS,
        )?
        .ok_or(AuthorityMaintenanceError(
            "authority_finalizer_commit_active_head_namespace_missing",
        ))?;
        let activations_readback = authenticate_restricted_commit_root_handle(
            &activations_root,
            RestrictedFinalizerCommitRootHandleKind::ParentNamespace,
        )?;
        if activations_readback.identity.volume_serial
            != parent.state_readback.identity.volume_serial
            || activations_readback.canonical_path_readback_sha256
                != canonical_path_sha256(&activations_canonical_path)
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_active_head_namespace_mismatch",
            ));
        }
        let file_canonical_path = format!("{activations_canonical_path}\\head.json");
        let file = open_relative_optional(
            &activations_root,
            "head.json",
            RESTRICTED_RECEIPT_READ_ACCESS,
        )?
        .ok_or(AuthorityMaintenanceError(
            "authority_finalizer_commit_active_head_missing",
        ))?;
        let file_capability_sha256 =
            authenticate_restricted_active_head_handle(&file, &file_canonical_path)?;
        let (file_bytes, file_identity, _) = read_bounded_stable(&file)?;
        let value = super::ProtectedActiveHead::parse_canonical(&file_bytes)?;
        if authenticate_restricted_active_head_handle(&file, &file_canonical_path)?
            != file_capability_sha256
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_active_head_authentication_drift",
            ));
        }
        parent.reverify()?;
        Ok(Self {
            activations_root,
            activations_readback,
            activations_canonical_path,
            file,
            file_identity,
            file_bytes,
            file_capability_sha256,
            file_canonical_path,
            value,
        })
    }

    fn revalidate(
        &self,
        parent: &RestrictedFinalizerCommitsParentRoot,
    ) -> Result<(), AuthorityMaintenanceError> {
        parent.reverify()?;
        let root_current = authenticate_restricted_commit_root_handle(
            &self.activations_root,
            RestrictedFinalizerCommitRootHandleKind::ParentNamespace,
        )?;
        if !same_authenticated_root_readback(&root_current, &self.activations_readback)
            || root_current.canonical_path_readback_sha256
                != canonical_path_sha256(&self.activations_canonical_path)
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_active_head_namespace_drift",
            ));
        }
        let capability =
            authenticate_restricted_active_head_handle(&self.file, &self.file_canonical_path)?;
        let (bytes, identity, _) = read_bounded_stable(&self.file)?;
        let parsed = super::ProtectedActiveHead::parse_canonical(&bytes)?;
        if capability != self.file_capability_sha256
            || identity != self.file_identity
            || bytes != self.file_bytes
            || parsed != self.value
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_active_head_held_drift",
            ));
        }
        let reopened = open_relative_optional(
            &self.activations_root,
            "head.json",
            RESTRICTED_RECEIPT_READ_ACCESS,
        )?
        .ok_or(AuthorityMaintenanceError(
            "authority_finalizer_commit_active_head_missing",
        ))?;
        let reopened_capability =
            authenticate_restricted_active_head_handle(&reopened, &self.file_canonical_path)?;
        let (reopened_bytes, reopened_identity, _) = read_bounded_stable(&reopened)?;
        if reopened_capability != self.file_capability_sha256
            || reopened_identity != self.file_identity
            || reopened_bytes != self.file_bytes
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_active_head_name_drift",
            ));
        }
        parent.reverify()
    }
}

pub(super) struct VerifiedPublishedFinalCommitLease {
    parent: RestrictedFinalizerCommitsParentRoot,
    active_head: RestrictedActiveHeadLease,
    transaction: RestrictedFinalizerCommitTransactionRoot,
    chain: StrictPublishedReceiptChain,
    readback: RecoveredFinalCommitReadback,
}

impl VerifiedPublishedFinalCommitLease {
    pub(super) fn binding(&self) -> FinalizerCommitBinding {
        self.readback.projection().binding()
    }

    pub(super) fn tip(&self) -> PersistedReceiptFileReference {
        self.readback.tip()
    }

    pub(super) fn projection(&self) -> FinalCommitPersistenceProjection {
        self.readback.projection()
    }

    pub(super) fn zero_residue(
        &self,
    ) -> super::finalizer_commit_protocol::OperationZeroResidueReadback {
        self.readback.projection().zero_residue()
    }

    pub(super) fn revalidate(&self) -> Result<(), AuthorityMaintenanceError> {
        self.parent.reverify()?;
        self.active_head.revalidate(&self.parent)?;
        self.transaction.verify_unchanged()?;
        self.chain.revalidate_held(&self.transaction)?;
        let fresh = self.transaction.strict_scan_exact(
            self.readback.active_head_transaction_sha256(),
            FinalizerCommitStage::FinalCommit,
        )?;
        let current = recovered_final_commit_readback(
            &fresh,
            self.readback.active_head_transaction_sha256(),
            self.transaction.authenticated_root_sha256(),
        )?;
        validate_final_commit_against_active_head(&current, &self.active_head.value)?;
        if current != self.readback {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_restricted_final_lease_drift",
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct RecoveredSealCompleteReadback {
    binding: FinalizerCommitBinding,
    active_head_transaction_sha256: [u8; 32],
    authenticated_root_sha256: [u8; 32],
    tip: PersistedReceiptFileReference,
    projection: SealCompletePersistenceProjection,
}

pub(super) struct VerifiedSealCompletePrecommitLease {
    parent: RestrictedFinalizerCommitsParentRoot,
    transaction: RestrictedFinalizerCommitTransactionRoot,
    chain: StrictPublishedReceiptChain,
    readback: RecoveredSealCompleteReadback,
}

impl VerifiedSealCompletePrecommitLease {
    pub(super) fn binding(&self) -> FinalizerCommitBinding {
        self.readback.binding
    }

    pub(super) fn tip(&self) -> PersistedReceiptFileReference {
        self.readback.tip
    }

    pub(super) fn projection(&self) -> SealCompletePersistenceProjection {
        self.readback.projection
    }

    pub(super) fn directive(&self) -> FinalizerCommitRecoveryDirective {
        FinalizerCommitRecoveryDirective::CompleteActiveHeadRuntimeAndPersistFinalCommit
    }

    pub(super) fn revalidate(&self) -> Result<(), AuthorityMaintenanceError> {
        self.parent.reverify()?;
        self.transaction.verify_unchanged()?;
        self.chain.revalidate_held(&self.transaction)?;
        let fresh = self.transaction.strict_scan_exact(
            self.readback.active_head_transaction_sha256,
            FinalizerCommitStage::SealComplete,
        )?;
        let current = recovered_seal_complete_readback(
            &fresh,
            self.readback.active_head_transaction_sha256,
            self.transaction.authenticated_root_sha256(),
        )?;
        if current != self.readback {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_restricted_precommit_lease_drift",
            ));
        }
        Ok(())
    }
}

enum RestrictedRuntimeLedgerAuthorityLease {
    Published(VerifiedPublishedFinalCommitLease),
    SealComplete(VerifiedSealCompletePrecommitLease),
}

impl RestrictedRuntimeLedgerAuthorityLease {
    fn parent(&self) -> &RestrictedFinalizerCommitsParentRoot {
        match self {
            Self::Published(value) => &value.parent,
            Self::SealComplete(value) => &value.parent,
        }
    }

    fn binding(&self) -> FinalizerCommitBinding {
        match self {
            Self::Published(value) => value.binding(),
            Self::SealComplete(value) => value.binding(),
        }
    }

    fn published_final_commit_receipt_sha256(&self) -> Result<[u8; 32], AuthorityMaintenanceError> {
        match self {
            Self::Published(value) => Ok(value.projection().final_commit_receipt_sha256()),
            Self::SealComplete(_) => Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_runtime_not_published",
            )),
        }
    }

    fn published_final_commit_projection(
        &self,
    ) -> Result<FinalCommitPersistenceProjection, AuthorityMaintenanceError> {
        match self {
            Self::Published(value) => Ok(value.projection()),
            Self::SealComplete(_) => Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_runtime_not_published",
            )),
        }
    }

    fn revalidate(&self) -> Result<(), AuthorityMaintenanceError> {
        match self {
            Self::Published(value) => value.revalidate(),
            Self::SealComplete(value) => value.revalidate(),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct RestrictedRuntimeLedgerFileIdentity {
    volume_serial: u64,
    file_id: [u8; 16],
    link_count: u32,
    attributes: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct RestrictedRuntimeLedgerFileReadback {
    identity: RestrictedRuntimeLedgerFileIdentity,
    canonical_path_readback_sha256: [u8; 32],
    complete_security_readback_sha256: [u8; 32],
    granted_access_readback_sha256: [u8; 32],
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct VerifiedPublishedRuntimeBindingProjection {
    capsule_sha256: [u8; 32],
    plan_sha256: [u8; 32],
    generation_sha256: [u8; 32],
    transaction_sha256: [u8; 32],
    final_commit_store_root_identity_sha256: [u8; 32],
    operation: super::AuthorityMaintenanceOperation,
    expected_worker_service_identity_sha256: [u8; 32],
    expected_worker_image_sha256: [u8; 32],
    exact_service_configuration_sha256: [u8; 32],
    expected_service_image_sha256: [u8; 32],
    expected_active_head_prior_sha256: [u8; 32],
    expected_active_head_replacement_sha256: [u8; 32],
    expected_activation_manifest_sha256: [u8; 32],
    expected_activation_epoch: u64,
    generation_object_manifest_sha256: [u8; 32],
    expected_runner_policy_state_byte_length: u64,
    expected_runner_policy_state_bytes_sha256: [u8; 32],
    expected_runner_policy_state_binding_sha256: [u8; 32],
    runner_policy_sealed_volume_serial: u64,
    runner_policy_sealed_file_id: [u8; 16],
    runner_policy_sealed_link_count: u32,
    runner_policy_sealed_attributes: u32,
    protected_blob_namespace_volume_serial: u64,
    protected_blob_namespace_file_id: [u8; 16],
    protected_blob_namespace_link_count: u32,
    protected_blob_namespace_attributes: u32,
    protected_blob_namespace_byte_length: u64,
    protected_blob_namespace_canonical_path_sha256: [u8; 32],
    protected_blob_namespace_initial_empty_inventory_sha256: [u8; 32],
    protected_blob_namespace_final_security_sha256: [u8; 32],
    protected_blob_file_security_sha256: [u8; 32],
    protected_blob_namespace_runtime_access: u32,
    protected_blob_namespace_share_access: u32,
    protected_blob_namespace_open_disposition: u32,
    protected_blob_file_create_access: u32,
    protected_blob_file_read_access: u32,
    protected_blob_file_cleanup_access: u32,
    protected_blob_namespace_seal_sha256: [u8; 32],
    residue_plan_sha256: [u8; 32],
    final_commit_gate_projection_sha256: [u8; 32],
    expected_final_commit_gate_sha256: [u8; 32],
}

impl VerifiedPublishedRuntimeBindingProjection {
    #[cfg(test)]
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn for_bootstrap_test(
        generation_sha256: [u8; 32],
        plan_sha256: [u8; 32],
        transaction_sha256: [u8; 32],
        operation: super::AuthorityMaintenanceOperation,
        exact_service_configuration_sha256: [u8; 32],
        expected_service_image_sha256: [u8; 32],
        expected_active_head_replacement_sha256: [u8; 32],
        expected_activation_manifest_sha256: [u8; 32],
        expected_activation_epoch: u64,
        expected_runner_policy_state_byte_length: u64,
        expected_runner_policy_state_bytes_sha256: [u8; 32],
        expected_runner_policy_state_binding_sha256: [u8; 32],
        runner_policy_sealed_volume_serial: u64,
        runner_policy_sealed_file_id: [u8; 16],
        runner_policy_sealed_link_count: u32,
        runner_policy_sealed_attributes: u32,
    ) -> Self {
        let protected_blob_namespace =
            ProtectedBlobNamespacePersistenceProjection::exact_test_fixture(
                generation_sha256,
                0x6c,
            );
        Self {
            capsule_sha256: [0x81; 32],
            plan_sha256,
            generation_sha256,
            transaction_sha256,
            final_commit_store_root_identity_sha256: [0x82; 32],
            operation,
            expected_worker_service_identity_sha256: [0x83; 32],
            expected_worker_image_sha256: [0x84; 32],
            exact_service_configuration_sha256,
            expected_service_image_sha256,
            expected_active_head_prior_sha256: [0x85; 32],
            expected_active_head_replacement_sha256,
            expected_activation_manifest_sha256,
            expected_activation_epoch,
            generation_object_manifest_sha256: [0x86; 32],
            expected_runner_policy_state_byte_length,
            expected_runner_policy_state_bytes_sha256,
            expected_runner_policy_state_binding_sha256,
            runner_policy_sealed_volume_serial,
            runner_policy_sealed_file_id,
            runner_policy_sealed_link_count,
            runner_policy_sealed_attributes,
            protected_blob_namespace_volume_serial: protected_blob_namespace.volume_serial(),
            protected_blob_namespace_file_id: protected_blob_namespace.file_id(),
            protected_blob_namespace_link_count: protected_blob_namespace.link_count(),
            protected_blob_namespace_attributes: protected_blob_namespace.attributes(),
            protected_blob_namespace_byte_length: protected_blob_namespace.byte_length(),
            protected_blob_namespace_canonical_path_sha256: protected_blob_namespace
                .canonical_path_sha256(),
            protected_blob_namespace_initial_empty_inventory_sha256: protected_blob_namespace
                .initial_empty_inventory_sha256(),
            protected_blob_namespace_final_security_sha256: protected_blob_namespace
                .final_security_sha256(),
            protected_blob_file_security_sha256: protected_blob_namespace.file_security_sha256(),
            protected_blob_namespace_runtime_access: protected_blob_namespace.runtime_access(),
            protected_blob_namespace_share_access: protected_blob_namespace.share_access(),
            protected_blob_namespace_open_disposition: protected_blob_namespace.open_disposition(),
            protected_blob_file_create_access: protected_blob_namespace.file_create_access(),
            protected_blob_file_read_access: protected_blob_namespace.file_read_access(),
            protected_blob_file_cleanup_access: protected_blob_namespace.file_cleanup_access(),
            protected_blob_namespace_seal_sha256: protected_blob_namespace.seal_sha256(),
            residue_plan_sha256: [0x87; 32],
            final_commit_gate_projection_sha256: [0x88; 32],
            expected_final_commit_gate_sha256: [0x89; 32],
        }
    }

    #[cfg(test)]
    pub(crate) const COMPLETE_FIELD_COUNT: usize = 41;

    #[cfg(test)]
    pub(crate) fn with_complete_field_drift_for_test(mut self, index: usize) -> Self {
        let drift = |value: &mut [u8; 32]| value[0] ^= 1;
        match index {
            0 => drift(&mut self.capsule_sha256),
            1 => drift(&mut self.plan_sha256),
            2 => drift(&mut self.generation_sha256),
            3 => drift(&mut self.transaction_sha256),
            4 => drift(&mut self.final_commit_store_root_identity_sha256),
            5 => self.operation = super::AuthorityMaintenanceOperation::Retire,
            6 => drift(&mut self.expected_worker_service_identity_sha256),
            7 => drift(&mut self.expected_worker_image_sha256),
            8 => drift(&mut self.exact_service_configuration_sha256),
            9 => drift(&mut self.expected_service_image_sha256),
            10 => drift(&mut self.expected_active_head_prior_sha256),
            11 => drift(&mut self.expected_active_head_replacement_sha256),
            12 => drift(&mut self.expected_activation_manifest_sha256),
            13 => self.expected_activation_epoch = self.expected_activation_epoch.saturating_add(1),
            14 => drift(&mut self.generation_object_manifest_sha256),
            15 => {
                self.expected_runner_policy_state_byte_length = self
                    .expected_runner_policy_state_byte_length
                    .saturating_add(1)
            }
            16 => drift(&mut self.expected_runner_policy_state_bytes_sha256),
            17 => drift(&mut self.expected_runner_policy_state_binding_sha256),
            18 => {
                self.runner_policy_sealed_volume_serial =
                    self.runner_policy_sealed_volume_serial.saturating_add(1)
            }
            19 => self.runner_policy_sealed_file_id[0] ^= 1,
            20 => {
                self.runner_policy_sealed_link_count =
                    self.runner_policy_sealed_link_count.saturating_add(1)
            }
            21 => self.runner_policy_sealed_attributes ^= 1,
            22 => {
                self.protected_blob_namespace_volume_serial = self
                    .protected_blob_namespace_volume_serial
                    .saturating_add(1)
            }
            23 => self.protected_blob_namespace_file_id[0] ^= 1,
            24 => {
                self.protected_blob_namespace_link_count =
                    self.protected_blob_namespace_link_count.saturating_add(1)
            }
            25 => self.protected_blob_namespace_attributes ^= 1,
            26 => {
                self.protected_blob_namespace_byte_length =
                    self.protected_blob_namespace_byte_length.saturating_add(1)
            }
            27 => drift(&mut self.protected_blob_namespace_canonical_path_sha256),
            28 => drift(&mut self.protected_blob_namespace_initial_empty_inventory_sha256),
            29 => drift(&mut self.protected_blob_namespace_final_security_sha256),
            30 => drift(&mut self.protected_blob_file_security_sha256),
            31 => self.protected_blob_namespace_runtime_access ^= 1,
            32 => self.protected_blob_namespace_share_access ^= 1,
            33 => self.protected_blob_namespace_open_disposition ^= 1,
            34 => self.protected_blob_file_create_access ^= 1,
            35 => self.protected_blob_file_read_access ^= 1,
            36 => self.protected_blob_file_cleanup_access ^= 1,
            37 => drift(&mut self.protected_blob_namespace_seal_sha256),
            38 => drift(&mut self.residue_plan_sha256),
            39 => drift(&mut self.final_commit_gate_projection_sha256),
            40 => drift(&mut self.expected_final_commit_gate_sha256),
            _ => panic!("complete projection test field index out of range"),
        }
        self
    }

    fn from_final_commit(
        projection: FinalCommitPersistenceProjection,
    ) -> Result<Self, AuthorityMaintenanceError> {
        Self::from_binding_and_identity(
            projection.binding(),
            projection.runner_policy_sealed_identity(),
            projection.protected_blob_namespace(),
        )
    }

    #[cfg(test)]
    fn from_binding(binding: FinalizerCommitBinding) -> Result<Self, AuthorityMaintenanceError> {
        Self::from_binding_and_identity(
            binding,
            RunnerPolicySealedIdentity::exact_test_fixture(0x6b),
            ProtectedBlobNamespacePersistenceProjection::exact_test_fixture(
                binding.generation_sha256(),
                0x6c,
            ),
        )
    }

    fn from_binding_and_identity(
        binding: FinalizerCommitBinding,
        runner_policy_sealed_identity: RunnerPolicySealedIdentity,
        protected_blob_namespace: ProtectedBlobNamespacePersistenceProjection,
    ) -> Result<Self, AuthorityMaintenanceError> {
        protected_blob_namespace.validate()?;
        if protected_blob_namespace.generation_sha256() != binding.generation_sha256() {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_blob_namespace_binding_mismatch",
            ));
        }
        let plan = binding.plan_binding();
        Ok(Self {
            capsule_sha256: binding.capsule_sha256(),
            plan_sha256: binding.plan_sha256(),
            generation_sha256: binding.generation_sha256(),
            transaction_sha256: binding.transaction_sha256(),
            final_commit_store_root_identity_sha256: binding
                .final_commit_store_root_identity_sha256(),
            operation: plan.operation(),
            expected_worker_service_identity_sha256: plan.expected_worker_service_identity_sha256(),
            expected_worker_image_sha256: plan.expected_worker_image_sha256(),
            exact_service_configuration_sha256: plan.exact_service_configuration_sha256(),
            expected_service_image_sha256: plan.expected_service_image_sha256(),
            expected_active_head_prior_sha256: plan.expected_active_head_prior_sha256(),
            expected_active_head_replacement_sha256: plan.expected_active_head_replacement_sha256(),
            expected_activation_manifest_sha256: plan.expected_activation_manifest_sha256(),
            expected_activation_epoch: plan.expected_activation_epoch(),
            generation_object_manifest_sha256: plan.generation_object_manifest_sha256(),
            expected_runner_policy_state_byte_length: plan
                .expected_runner_policy_state_byte_length(),
            expected_runner_policy_state_bytes_sha256: plan
                .expected_runner_policy_state_bytes_sha256(),
            expected_runner_policy_state_binding_sha256: plan
                .expected_runner_policy_state_binding_sha256(),
            runner_policy_sealed_volume_serial: runner_policy_sealed_identity.volume_serial(),
            runner_policy_sealed_file_id: runner_policy_sealed_identity.file_id(),
            runner_policy_sealed_link_count: runner_policy_sealed_identity.link_count(),
            runner_policy_sealed_attributes: runner_policy_sealed_identity.attributes(),
            protected_blob_namespace_volume_serial: protected_blob_namespace.volume_serial(),
            protected_blob_namespace_file_id: protected_blob_namespace.file_id(),
            protected_blob_namespace_link_count: protected_blob_namespace.link_count(),
            protected_blob_namespace_attributes: protected_blob_namespace.attributes(),
            protected_blob_namespace_byte_length: protected_blob_namespace.byte_length(),
            protected_blob_namespace_canonical_path_sha256: protected_blob_namespace
                .canonical_path_sha256(),
            protected_blob_namespace_initial_empty_inventory_sha256: protected_blob_namespace
                .initial_empty_inventory_sha256(),
            protected_blob_namespace_final_security_sha256: protected_blob_namespace
                .final_security_sha256(),
            protected_blob_file_security_sha256: protected_blob_namespace.file_security_sha256(),
            protected_blob_namespace_runtime_access: protected_blob_namespace.runtime_access(),
            protected_blob_namespace_share_access: protected_blob_namespace.share_access(),
            protected_blob_namespace_open_disposition: protected_blob_namespace.open_disposition(),
            protected_blob_file_create_access: protected_blob_namespace.file_create_access(),
            protected_blob_file_read_access: protected_blob_namespace.file_read_access(),
            protected_blob_file_cleanup_access: protected_blob_namespace.file_cleanup_access(),
            protected_blob_namespace_seal_sha256: protected_blob_namespace.seal_sha256(),
            residue_plan_sha256: plan.residue_plan_sha256()?,
            final_commit_gate_projection_sha256: binding.final_commit_gate_projection_sha256(),
            expected_final_commit_gate_sha256: binding.expected_final_commit_gate_sha256(),
        })
    }

    pub(crate) fn capsule_sha256(self) -> [u8; 32] {
        self.capsule_sha256
    }

    pub(crate) fn plan_sha256(self) -> [u8; 32] {
        self.plan_sha256
    }

    pub(crate) fn generation_sha256(self) -> [u8; 32] {
        self.generation_sha256
    }

    pub(crate) fn transaction_sha256(self) -> [u8; 32] {
        self.transaction_sha256
    }

    pub(crate) fn final_commit_store_root_identity_sha256(self) -> [u8; 32] {
        self.final_commit_store_root_identity_sha256
    }

    pub(crate) fn operation(self) -> super::AuthorityMaintenanceOperation {
        self.operation
    }

    pub(crate) fn expected_worker_service_identity_sha256(self) -> [u8; 32] {
        self.expected_worker_service_identity_sha256
    }

    pub(crate) fn expected_worker_image_sha256(self) -> [u8; 32] {
        self.expected_worker_image_sha256
    }

    pub(crate) fn exact_service_configuration_sha256(self) -> [u8; 32] {
        self.exact_service_configuration_sha256
    }

    pub(crate) fn expected_service_image_sha256(self) -> [u8; 32] {
        self.expected_service_image_sha256
    }

    pub(crate) fn expected_active_head_prior_sha256(self) -> [u8; 32] {
        self.expected_active_head_prior_sha256
    }

    pub(crate) fn expected_active_head_replacement_sha256(self) -> [u8; 32] {
        self.expected_active_head_replacement_sha256
    }

    pub(crate) fn expected_activation_manifest_sha256(self) -> [u8; 32] {
        self.expected_activation_manifest_sha256
    }

    pub(crate) fn expected_activation_epoch(self) -> u64 {
        self.expected_activation_epoch
    }

    pub(crate) fn generation_object_manifest_sha256(self) -> [u8; 32] {
        self.generation_object_manifest_sha256
    }

    pub(crate) fn expected_runner_policy_state_byte_length(self) -> u64 {
        self.expected_runner_policy_state_byte_length
    }

    pub(crate) fn expected_runner_policy_state_bytes_sha256(self) -> [u8; 32] {
        self.expected_runner_policy_state_bytes_sha256
    }

    pub(crate) fn expected_runner_policy_state_binding_sha256(self) -> [u8; 32] {
        self.expected_runner_policy_state_binding_sha256
    }

    pub(crate) fn runner_policy_sealed_volume_serial(self) -> u64 {
        self.runner_policy_sealed_volume_serial
    }

    pub(crate) fn runner_policy_sealed_file_id(self) -> [u8; 16] {
        self.runner_policy_sealed_file_id
    }

    pub(crate) fn runner_policy_sealed_link_count(self) -> u32 {
        self.runner_policy_sealed_link_count
    }

    pub(crate) fn runner_policy_sealed_attributes(self) -> u32 {
        self.runner_policy_sealed_attributes
    }

    pub(crate) fn protected_blob_namespace_seal_sha256(self) -> [u8; 32] {
        self.protected_blob_namespace_seal_sha256
    }

    pub(crate) fn residue_plan_sha256(self) -> [u8; 32] {
        self.residue_plan_sha256
    }

    pub(crate) fn final_commit_gate_projection_sha256(self) -> [u8; 32] {
        self.final_commit_gate_projection_sha256
    }

    pub(crate) fn expected_final_commit_gate_sha256(self) -> [u8; 32] {
        self.expected_final_commit_gate_sha256
    }

    /// Digest every admitted field so downstream runtime bindings cannot
    /// silently select only an older subset when this projection grows.
    pub(crate) fn complete_binding_sha256(self) -> [u8; 32] {
        let operation = match self.operation {
            super::AuthorityMaintenanceOperation::Install => 1u8,
            super::AuthorityMaintenanceOperation::Update => 2u8,
            super::AuthorityMaintenanceOperation::Retire => 3u8,
        };
        let mut digest = Sha256::new();
        digest.update(PUBLISHED_RUNTIME_BINDING_PROJECTION_DOMAIN);
        digest.update(self.capsule_sha256);
        digest.update(self.plan_sha256);
        digest.update(self.generation_sha256);
        digest.update(self.transaction_sha256);
        digest.update(self.final_commit_store_root_identity_sha256);
        digest.update([operation]);
        digest.update(self.expected_worker_service_identity_sha256);
        digest.update(self.expected_worker_image_sha256);
        digest.update(self.exact_service_configuration_sha256);
        digest.update(self.expected_service_image_sha256);
        digest.update(self.expected_active_head_prior_sha256);
        digest.update(self.expected_active_head_replacement_sha256);
        digest.update(self.expected_activation_manifest_sha256);
        digest.update(self.expected_activation_epoch.to_be_bytes());
        digest.update(self.generation_object_manifest_sha256);
        digest.update(self.expected_runner_policy_state_byte_length.to_be_bytes());
        digest.update(self.expected_runner_policy_state_bytes_sha256);
        digest.update(self.expected_runner_policy_state_binding_sha256);
        digest.update(self.runner_policy_sealed_volume_serial.to_be_bytes());
        digest.update(self.runner_policy_sealed_file_id);
        digest.update(self.runner_policy_sealed_link_count.to_be_bytes());
        digest.update(self.runner_policy_sealed_attributes.to_be_bytes());
        digest.update(self.protected_blob_namespace_volume_serial.to_be_bytes());
        digest.update(self.protected_blob_namespace_file_id);
        digest.update(self.protected_blob_namespace_link_count.to_be_bytes());
        digest.update(self.protected_blob_namespace_attributes.to_be_bytes());
        digest.update(self.protected_blob_namespace_byte_length.to_be_bytes());
        digest.update(self.protected_blob_namespace_canonical_path_sha256);
        digest.update(self.protected_blob_namespace_initial_empty_inventory_sha256);
        digest.update(self.protected_blob_namespace_final_security_sha256);
        digest.update(self.protected_blob_file_security_sha256);
        digest.update(self.protected_blob_namespace_runtime_access.to_be_bytes());
        digest.update(self.protected_blob_namespace_share_access.to_be_bytes());
        digest.update(self.protected_blob_namespace_open_disposition.to_be_bytes());
        digest.update(self.protected_blob_file_create_access.to_be_bytes());
        digest.update(self.protected_blob_file_read_access.to_be_bytes());
        digest.update(self.protected_blob_file_cleanup_access.to_be_bytes());
        digest.update(self.protected_blob_namespace_seal_sha256);
        digest.update(self.residue_plan_sha256);
        digest.update(self.final_commit_gate_projection_sha256);
        digest.update(self.expected_final_commit_gate_sha256);
        digest.finalize().into()
    }
}

/// Non-copy provenance for the authenticated state/generation namespace. This
/// lease remains owned by the ledger after the exact file pair is adopted.
struct VerifiedRuntimeLedgerNamespaceLease {
    authority: RestrictedRuntimeLedgerAuthorityLease,
    generations_root: OwnedHandle,
    generations_readback: AuthenticatedFinalizerCommitRootReadback,
    generations_canonical_path: String,
    generation_root: OwnedHandle,
    generation_readback: AuthenticatedFinalizerCommitRootReadback,
    generation_canonical_path: String,
    generation_sha256: [u8; 32],
    ledger_readback: RestrictedRuntimeLedgerFileReadback,
    ledger_path: PathBuf,
    ledger_anchor_readback: RestrictedRuntimeLedgerFileReadback,
    ledger_anchor_path: PathBuf,
}

/// Non-clone runtime-write capability for the fixed protected blob namespace.
/// It can only be minted while a verified published FinalCommit lease and its
/// already-held generation directory agree with the persisted seal projection.
pub(crate) struct VerifiedPublishedProtectedBlobNamespaceLease {
    root: OwnedHandle,
    readback: AuthenticatedFinalizerCommitRootReadback,
    canonical_path: PathBuf,
    projection: ProtectedBlobNamespacePersistenceProjection,
}

impl VerifiedPublishedProtectedBlobNamespaceLease {
    fn open(
        pair: &VerifiedRuntimeLedgerPair,
        projection: ProtectedBlobNamespacePersistenceProjection,
    ) -> Result<Self, AuthorityMaintenanceError> {
        pair.revalidate()?;
        projection.validate()?;
        if projection.generation_sha256() != pair.generation_sha256()
            || projection.runtime_access() != RESTRICTED_RUNTIME_BLOB_DIRECTORY_ACCESS
            || projection.share_access() != RESTRICTED_RUNTIME_BLOB_DIRECTORY_SHARE_ACCESS
            || projection.open_disposition() != FILE_OPEN
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_blob_namespace_contract_invalid",
            ));
        }
        let canonical_path = pair
            .namespace
            .generation_canonical_path
            .parse::<PathBuf>()
            .map_err(|_| {
                AuthorityMaintenanceError("authority_finalizer_commit_blob_namespace_path_invalid")
            })?
            .join(AUTHORITY_PROTECTED_BLOB_NAMESPACE_DIRECTORY_NAME);
        let canonical_path_text = canonical_path.to_str().ok_or(AuthorityMaintenanceError(
            "authority_finalizer_commit_blob_namespace_path_invalid",
        ))?;
        let root = nt_open_relative_directory_with_share(
            &pair.namespace.generation_root,
            AUTHORITY_PROTECTED_BLOB_NAMESPACE_DIRECTORY_NAME,
            RESTRICTED_RUNTIME_BLOB_DIRECTORY_ACCESS,
            RESTRICTED_RUNTIME_BLOB_DIRECTORY_SHARE_ACCESS,
        )?
        .ok_or(AuthorityMaintenanceError(
            "authority_finalizer_commit_blob_namespace_missing",
        ))?;
        let readback = authenticate_restricted_commit_root_handle(
            &root,
            RestrictedFinalizerCommitRootHandleKind::RuntimeBlobDirectory,
        )?;
        if readback.identity.volume_serial != projection.volume_serial()
            || readback.identity.file_id != projection.file_id()
            || readback.identity.link_count != projection.link_count()
            || readback.identity.attributes != projection.attributes()
            || readback.identity.byte_length != projection.byte_length()
            || readback.complete_security_readback_sha256 != projection.final_security_sha256()
            || protected_blob_canonical_path_sha256(canonical_path_text)
                != projection.canonical_path_sha256()
            || readback.canonical_path_readback_sha256 != canonical_path_sha256(canonical_path_text)
            || readback.identity.volume_serial
                != pair.namespace.generation_readback.identity.volume_serial
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_blob_namespace_seal_mismatch",
            ));
        }
        let value = Self {
            root,
            readback,
            canonical_path,
            projection,
        };
        value.revalidate(&pair.namespace)?;
        pair.revalidate()?;
        Ok(value)
    }

    fn revalidate(
        &self,
        namespace: &VerifiedRuntimeLedgerNamespaceLease,
    ) -> Result<(), AuthorityMaintenanceError> {
        namespace.revalidate()?;
        self.projection.validate()?;
        let current = authenticate_restricted_commit_root_handle(
            &self.root,
            RestrictedFinalizerCommitRootHandleKind::RuntimeBlobDirectory,
        )?;
        let canonical_path = self
            .canonical_path
            .to_str()
            .ok_or(AuthorityMaintenanceError(
                "authority_finalizer_commit_blob_namespace_path_invalid",
            ))?;
        if current != self.readback
            || current.canonical_path_readback_sha256 != canonical_path_sha256(canonical_path)
            || current.identity.volume_serial
                != namespace.generation_readback.identity.volume_serial
            || self.projection.generation_sha256() != namespace.generation_sha256
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_blob_namespace_drift",
            ));
        }
        Ok(())
    }

    pub(crate) fn projection(&self) -> ProtectedBlobNamespacePersistenceProjection {
        self.projection
    }

    pub(crate) fn into_adoption(self) -> VerifiedPublishedProtectedBlobNamespaceAdoption {
        VerifiedPublishedProtectedBlobNamespaceAdoption {
            root: self.root,
            canonical_path: self.canonical_path,
            projection: self.projection,
        }
    }
}

#[must_use]
pub(crate) struct VerifiedPublishedProtectedBlobNamespaceAdoption {
    root: OwnedHandle,
    canonical_path: PathBuf,
    projection: ProtectedBlobNamespacePersistenceProjection,
}

impl VerifiedPublishedProtectedBlobNamespaceAdoption {
    pub(crate) fn consume_with<T>(
        self,
        consumer: impl FnOnce(OwnedHandle, PathBuf, ProtectedBlobNamespacePersistenceProjection) -> T,
    ) -> T {
        consumer(self.root, self.canonical_path, self.projection)
    }
}

/// Opaque authenticated pair minted only from a verified terminal commit
/// lease. Sibling modules can consume it, but cannot construct it from raw
/// files, paths, digests, or booleans.
struct VerifiedRuntimeLedgerPair {
    namespace: VerifiedRuntimeLedgerNamespaceLease,
    ledger: std::fs::File,
    ledger_anchor: std::fs::File,
}

impl VerifiedRuntimeLedgerPair {
    fn open(
        authority: RestrictedRuntimeLedgerAuthorityLease,
    ) -> Result<Self, AuthorityMaintenanceError> {
        authority.revalidate()?;
        let generation_sha256 = authority.binding().generation_sha256();
        if is_zero(&generation_sha256) {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_runtime_generation_invalid",
            ));
        }
        let parent = authority.parent();
        let generations_canonical_path = format!("{}\\generations", parent.state_canonical_path);
        let generations_root = nt_open_relative_directory(
            &parent.state_root,
            "generations",
            RESTRICTED_RUNTIME_GENERATION_ROOT_ACCESS,
        )?
        .ok_or(AuthorityMaintenanceError(
            "authority_finalizer_commit_runtime_generations_missing",
        ))?;
        let generations_readback = authenticate_restricted_commit_root_handle(
            &generations_root,
            RestrictedFinalizerCommitRootHandleKind::GenerationNamespace,
        )?;
        validate_runtime_generation_root_provenance(
            &generations_readback,
            &parent.state_readback,
            &generations_canonical_path,
        )?;

        let generation_name = hex_lower(&generation_sha256);
        let generation_canonical_path = format!("{generations_canonical_path}\\{generation_name}");
        let generation_root = nt_open_relative_directory(
            &generations_root,
            &generation_name,
            RESTRICTED_RUNTIME_GENERATION_ROOT_ACCESS,
        )?
        .ok_or(AuthorityMaintenanceError(
            "authority_finalizer_commit_runtime_generation_missing",
        ))?;
        let generation_readback = authenticate_restricted_commit_root_handle(
            &generation_root,
            RestrictedFinalizerCommitRootHandleKind::SealedGeneration,
        )?;
        validate_runtime_generation_root_provenance(
            &generation_readback,
            &generations_readback,
            &generation_canonical_path,
        )?;

        let ledger_canonical_path = format!("{generation_canonical_path}\\ledger.bin");
        let ledger = nt_open_relative_with_share(
            &generation_root,
            "ledger.bin",
            FILE_OPEN,
            RESTRICTED_RUNTIME_LEDGER_ACCESS,
            None,
            RESTRICTED_RUNTIME_LEDGER_SHARE_ACCESS,
        )?
        .ok_or(AuthorityMaintenanceError(
            "authority_finalizer_commit_runtime_ledger_missing",
        ))?;
        let ledger_readback = authenticate_restricted_runtime_ledger_handle(
            &ledger,
            &ledger_canonical_path,
            generation_readback.identity.volume_serial,
        )?;

        let ledger_anchor_canonical_path =
            format!("{generation_canonical_path}\\ledger.bin.anchor");
        let ledger_anchor = nt_open_relative_with_share(
            &generation_root,
            "ledger.bin.anchor",
            FILE_OPEN,
            RESTRICTED_RUNTIME_LEDGER_ACCESS,
            None,
            RESTRICTED_RUNTIME_LEDGER_SHARE_ACCESS,
        )?
        .ok_or(AuthorityMaintenanceError(
            "authority_finalizer_commit_runtime_ledger_anchor_missing",
        ))?;
        let ledger_anchor_readback = authenticate_restricted_runtime_ledger_handle(
            &ledger_anchor,
            &ledger_anchor_canonical_path,
            generation_readback.identity.volume_serial,
        )?;
        if same_runtime_ledger_file_identity(
            &ledger_readback.identity,
            &ledger_anchor_readback.identity,
        ) {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_runtime_ledger_pair_aliased",
            ));
        }
        authority.revalidate()?;
        let namespace = VerifiedRuntimeLedgerNamespaceLease {
            authority,
            generations_root,
            generations_readback,
            generations_canonical_path,
            generation_root,
            generation_readback,
            generation_canonical_path,
            generation_sha256,
            ledger_readback,
            ledger_path: PathBuf::from(ledger_canonical_path),
            ledger_anchor_readback,
            ledger_anchor_path: PathBuf::from(ledger_anchor_canonical_path),
        };
        Ok(Self {
            namespace,
            ledger: std::fs::File::from(ledger),
            ledger_anchor: std::fs::File::from(ledger_anchor),
        })
    }

    fn generation_sha256(&self) -> [u8; 32] {
        self.namespace.generation_sha256
    }

    fn revalidate(&self) -> Result<(), AuthorityMaintenanceError> {
        self.namespace
            .revalidate_pair(&self.ledger, &self.ledger_anchor)
    }

    fn into_parts(
        self,
    ) -> Result<
        (
            VerifiedRuntimeLedgerNamespaceLease,
            std::fs::File,
            PathBuf,
            std::fs::File,
            PathBuf,
        ),
        AuthorityMaintenanceError,
    > {
        self.revalidate()?;
        let ledger_path = self.namespace.ledger_path.clone();
        let ledger_anchor_path = self.namespace.ledger_anchor_path.clone();
        let Self {
            namespace,
            ledger,
            ledger_anchor,
        } = self;
        Ok((
            namespace,
            ledger,
            ledger_path,
            ledger_anchor,
            ledger_anchor_path,
        ))
    }
}

impl VerifiedRuntimeLedgerNamespaceLease {
    fn generation_sha256(&self) -> [u8; 32] {
        self.generation_sha256
    }

    fn revalidate(&self) -> Result<(), AuthorityMaintenanceError> {
        self.authority.revalidate()?;
        let generations_current = authenticate_restricted_commit_root_handle(
            &self.generations_root,
            RestrictedFinalizerCommitRootHandleKind::GenerationNamespace,
        )?;
        let generation_current = authenticate_restricted_commit_root_handle(
            &self.generation_root,
            RestrictedFinalizerCommitRootHandleKind::SealedGeneration,
        )?;
        if !same_authenticated_root_readback(&generations_current, &self.generations_readback)
            || !same_authenticated_root_readback(&generation_current, &self.generation_readback)
            || generations_current.canonical_path_readback_sha256
                != canonical_path_sha256(&self.generations_canonical_path)
            || generation_current.canonical_path_readback_sha256
                != canonical_path_sha256(&self.generation_canonical_path)
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_runtime_ledger_provenance_drift",
            ));
        }
        Ok(())
    }

    fn revalidate_pair(
        &self,
        ledger: &std::fs::File,
        ledger_anchor: &std::fs::File,
    ) -> Result<(), AuthorityMaintenanceError> {
        self.revalidate()?;
        let ledger_current = authenticate_restricted_runtime_ledger_handle(
            ledger,
            self.ledger_path.to_str().ok_or(AuthorityMaintenanceError(
                "authority_finalizer_commit_runtime_ledger_path_invalid",
            ))?,
            self.generation_readback.identity.volume_serial,
        )?;
        let anchor_current = authenticate_restricted_runtime_ledger_handle(
            ledger_anchor,
            self.ledger_anchor_path
                .to_str()
                .ok_or(AuthorityMaintenanceError(
                    "authority_finalizer_commit_runtime_ledger_path_invalid",
                ))?,
            self.generation_readback.identity.volume_serial,
        )?;
        if ledger_current != self.ledger_readback
            || anchor_current != self.ledger_anchor_readback
            || same_runtime_ledger_file_identity(&ledger_current.identity, &anchor_current.identity)
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_runtime_ledger_pair_drift",
            ));
        }
        Ok(())
    }
}

pub(crate) struct VerifiedPublishedRuntimeLedgerNamespaceLease {
    inner: VerifiedRuntimeLedgerNamespaceLease,
}

impl VerifiedPublishedRuntimeLedgerNamespaceLease {
    pub(crate) fn generation_sha256(&self) -> [u8; 32] {
        self.inner.generation_sha256()
    }

    pub(crate) fn binding_projection(
        &self,
    ) -> Result<VerifiedPublishedRuntimeBindingProjection, AuthorityMaintenanceError> {
        VerifiedPublishedRuntimeBindingProjection::from_final_commit(
            self.inner.authority.published_final_commit_projection()?,
        )
    }

    pub(crate) fn revalidate(&self) -> Result<(), AuthorityMaintenanceError> {
        self.inner.revalidate()
    }

    pub(crate) fn revalidate_pair(
        &self,
        ledger: &std::fs::File,
        ledger_anchor: &std::fs::File,
    ) -> Result<(), AuthorityMaintenanceError> {
        self.inner.revalidate_pair(ledger, ledger_anchor)
    }
}

pub(crate) struct VerifiedPublishedRuntimeLedgerPair {
    inner: VerifiedRuntimeLedgerPair,
    protected_blob_namespace: VerifiedPublishedProtectedBlobNamespaceLease,
}

impl VerifiedPublishedRuntimeLedgerPair {
    pub(crate) fn generation_sha256(&self) -> [u8; 32] {
        self.inner.generation_sha256()
    }

    pub(crate) fn binding_projection(
        &self,
    ) -> Result<VerifiedPublishedRuntimeBindingProjection, AuthorityMaintenanceError> {
        VerifiedPublishedRuntimeBindingProjection::from_final_commit(
            self.inner
                .namespace
                .authority
                .published_final_commit_projection()?,
        )
    }

    pub(crate) fn final_commit_receipt_sha256(
        &self,
    ) -> Result<[u8; 32], AuthorityMaintenanceError> {
        self.inner
            .namespace
            .authority
            .published_final_commit_receipt_sha256()
    }

    pub(crate) fn revalidate(&self) -> Result<(), AuthorityMaintenanceError> {
        self.inner.revalidate()?;
        self.protected_blob_namespace
            .revalidate(&self.inner.namespace)
    }

    pub(crate) fn into_storage_set(
        self,
    ) -> Result<VerifiedPublishedRuntimeStorageSet, AuthorityMaintenanceError> {
        self.revalidate()?;
        let protected_blob_namespace = self.protected_blob_namespace;
        let (namespace, ledger, ledger_path, ledger_anchor, ledger_anchor_path) =
            self.inner.into_parts()?;
        Ok(VerifiedPublishedRuntimeStorageSet {
            namespace: VerifiedPublishedRuntimeLedgerNamespaceLease { inner: namespace },
            protected_blob_namespace,
            ledger,
            ledger_path,
            ledger_anchor,
            ledger_anchor_path,
        })
    }
}

/// Opaque, non-clone storage handoff produced only by a verified published
/// FinalCommit. The ledger pair and fixed blob namespace remain one provenance
/// unit until the authority performs its first replay.
pub(crate) struct VerifiedPublishedRuntimeStorageSet {
    namespace: VerifiedPublishedRuntimeLedgerNamespaceLease,
    protected_blob_namespace: VerifiedPublishedProtectedBlobNamespaceLease,
    ledger: std::fs::File,
    ledger_path: PathBuf,
    ledger_anchor: std::fs::File,
    ledger_anchor_path: PathBuf,
}

impl VerifiedPublishedRuntimeStorageSet {
    pub(crate) fn revalidate(&self) -> Result<(), AuthorityMaintenanceError> {
        self.namespace
            .revalidate_pair(&self.ledger, &self.ledger_anchor)?;
        self.protected_blob_namespace
            .revalidate(&self.namespace.inner)
    }

    pub(crate) fn into_adoption(
        self,
    ) -> Result<VerifiedPublishedRuntimeAdoption, AuthorityMaintenanceError> {
        self.revalidate()?;
        Ok(VerifiedPublishedRuntimeAdoption {
            namespace: self.namespace,
            protected_blob_namespace: self.protected_blob_namespace.into_adoption(),
            ledger: self.ledger,
            ledger_path: self.ledger_path,
            ledger_anchor: self.ledger_anchor,
            ledger_anchor_path: self.ledger_anchor_path,
        })
    }
}

#[must_use]
pub(crate) struct VerifiedPublishedRuntimeAdoption {
    namespace: VerifiedPublishedRuntimeLedgerNamespaceLease,
    protected_blob_namespace: VerifiedPublishedProtectedBlobNamespaceAdoption,
    ledger: std::fs::File,
    ledger_path: PathBuf,
    ledger_anchor: std::fs::File,
    ledger_anchor_path: PathBuf,
}

impl VerifiedPublishedRuntimeAdoption {
    pub(crate) fn consume_with<T, E>(
        self,
        consumer: impl FnOnce(
            VerifiedPublishedRuntimeLedgerNamespaceLease,
            VerifiedPublishedProtectedBlobNamespaceAdoption,
            std::fs::File,
            PathBuf,
            std::fs::File,
            PathBuf,
        ) -> Result<T, E>,
    ) -> Result<T, E> {
        consumer(
            self.namespace,
            self.protected_blob_namespace,
            self.ledger,
            self.ledger_path,
            self.ledger_anchor,
            self.ledger_anchor_path,
        )
    }
}

pub(super) struct VerifiedPrecommitRuntimeLedgerNamespaceLease {
    inner: VerifiedRuntimeLedgerNamespaceLease,
}

impl VerifiedPrecommitRuntimeLedgerNamespaceLease {
    pub(super) fn generation_sha256(&self) -> [u8; 32] {
        self.inner.generation_sha256()
    }

    pub(super) fn revalidate(&self) -> Result<(), AuthorityMaintenanceError> {
        self.inner.revalidate()
    }

    pub(super) fn revalidate_pair(
        &self,
        ledger: &std::fs::File,
        ledger_anchor: &std::fs::File,
    ) -> Result<(), AuthorityMaintenanceError> {
        self.inner.revalidate_pair(ledger, ledger_anchor)
    }
}

pub(super) struct VerifiedPrecommitRuntimeLedgerPair {
    inner: VerifiedRuntimeLedgerPair,
}

impl VerifiedPrecommitRuntimeLedgerPair {
    pub(super) fn generation_sha256(&self) -> [u8; 32] {
        self.inner.generation_sha256()
    }

    pub(super) fn revalidate(&self) -> Result<(), AuthorityMaintenanceError> {
        self.inner.revalidate()
    }

    pub(super) fn into_parts(
        self,
    ) -> Result<
        (
            VerifiedPrecommitRuntimeLedgerNamespaceLease,
            std::fs::File,
            PathBuf,
            std::fs::File,
            PathBuf,
        ),
        AuthorityMaintenanceError,
    > {
        let (namespace, ledger, ledger_path, ledger_anchor, ledger_anchor_path) =
            self.inner.into_parts()?;
        Ok((
            VerifiedPrecommitRuntimeLedgerNamespaceLease { inner: namespace },
            ledger,
            ledger_path,
            ledger_anchor,
            ledger_anchor_path,
        ))
    }
}

impl VerifiedPublishedFinalCommitLease {
    pub(super) fn into_held_runtime_ledger_pair(
        self,
    ) -> Result<VerifiedPublishedRuntimeLedgerPair, AuthorityMaintenanceError> {
        let protected_blob_projection = self.projection().protected_blob_namespace();
        let inner = VerifiedRuntimeLedgerPair::open(
            RestrictedRuntimeLedgerAuthorityLease::Published(self),
        )?;
        let protected_blob_namespace =
            VerifiedPublishedProtectedBlobNamespaceLease::open(&inner, protected_blob_projection)?;
        Ok(VerifiedPublishedRuntimeLedgerPair {
            inner,
            protected_blob_namespace,
        })
    }
}

impl VerifiedSealCompletePrecommitLease {
    pub(super) fn into_held_runtime_ledger_pair(
        self,
    ) -> Result<VerifiedPrecommitRuntimeLedgerPair, AuthorityMaintenanceError> {
        Ok(VerifiedPrecommitRuntimeLedgerPair {
            inner: VerifiedRuntimeLedgerPair::open(
                RestrictedRuntimeLedgerAuthorityLease::SealComplete(self),
            )?,
        })
    }
}

/// Capability issued only by the held-parent factory after it has opened the
/// canonical protected transaction root relative to that parent and verified
/// its complete security descriptor plus kernel-granted access. Accepting a
/// path or caller-supplied booleans here would collapse that trust boundary.
pub(super) struct AuthenticatedFinalizerCommitStoreRoot {
    root: OwnedHandle,
    readback: AuthenticatedFinalizerCommitRootReadback,
    namespace_root: OwnedHandle,
    namespace_readback: AuthenticatedFinalizerCommitRootReadback,
    canonical_path: String,
    root_reverify: RootReverify,
    namespace_root_reverify: RootReverify,
    receipt_reverify: ReceiptReverify,
    generation_progress_root: Option<AuthenticatedFinalizerGenerationProgressRoot>,
}

pub(super) struct AuthenticatedFinalizerGenerationProgressRoot {
    root: OwnedHandle,
    canonical_path: String,
    final_commit_store_root_identity_sha256: [u8; 32],
}

impl AuthenticatedFinalizerGenerationProgressRoot {
    pub(super) fn into_parts(self) -> (OwnedHandle, String, [u8; 32]) {
        (
            self.root,
            self.canonical_path,
            self.final_commit_store_root_identity_sha256,
        )
    }
}

impl AuthenticatedFinalizerCommitStoreRoot {
    pub(super) fn authenticated_root_sha256(&self) -> [u8; 32] {
        authenticated_root_digest(&self.readback)
    }

    pub(super) fn take_generation_progress_root(
        &mut self,
    ) -> Result<AuthenticatedFinalizerGenerationProgressRoot, AuthorityMaintenanceError> {
        self.generation_progress_root
            .take()
            .ok_or(AuthorityMaintenanceError(
                "authority_finalizer_commit_generation_progress_root_already_taken",
            ))
    }

    fn self_authenticate_transaction_started_binding(
        &self,
        active_head_transaction_sha256: [u8; 32],
    ) -> Result<FinalizerCommitBinding, AuthorityMaintenanceError> {
        let current = (self.root_reverify)(&self.root)?;
        if !same_authenticated_root_readback(&current, &self.readback) {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_root_authentication_drift",
            ));
        }
        let final_name = stage_file_name(FinalizerCommitStage::TransactionStarted);
        let final_path = receipt_canonical_path(&self.canonical_path, final_name)?;
        let file =
            open_relative_optional(&self.root, final_name, AUTHENTICATED_RECEIPT_READ_ACCESS)?
                .ok_or(AuthorityMaintenanceError(
                    "authority_finalizer_commit_transaction_started_missing",
                ))?;
        let security_phase =
            classify_final_receipt_security(privileged_complete_security_sha256(&file)?)?;
        let kind = match security_phase {
            FinalReceiptSecurityPhase::PrivateSealed => {
                FinalizerCommitReceiptHandleKind::PublishingInspectionSealed
            }
            FinalReceiptSecurityPhase::PublishedImmutable => {
                FinalizerCommitReceiptHandleKind::PublishedReadOnly
            }
        };
        let capability =
            verify_receipt_capability(&file, kind, Some(self.receipt_reverify), &final_path)?;
        let (bytes, _, _) = read_bounded_stable(&file)?;
        let envelope =
            DurableFinalizerCommitEnvelope::parse_transaction_started_self_authenticated(
                &bytes,
                self.authenticated_root_sha256(),
                active_head_transaction_sha256,
            )?;
        require_same_receipt_capability(
            &file,
            kind,
            Some(self.receipt_reverify),
            capability,
            &final_path,
        )?;
        let final_root = (self.root_reverify)(&self.root)?;
        if !same_authenticated_root_readback(&final_root, &self.readback) {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_root_authentication_drift",
            ));
        }
        Ok(envelope.binding)
    }
}

impl NativeElevatedFinalizerCommitLease {
    fn revalidate(
        &self,
        store: &FinalizerCommitReceiptStore,
        expected_durable_stage: FinalizerCommitStage,
    ) -> Result<(), AuthorityMaintenanceError> {
        if self.binding != store.binding
            || self.authenticated_root_sha256 != store.authenticated_root_sha256
            || is_zero(&self.authenticated_root_sha256)
            || self.process_id == 0
            || self.process_creation_time == 0
            || self.session_id == 0
            || self.image_identity.volume_serial == 0
            || self.image_identity.file_id.iter().all(|byte| *byte == 0)
            || self.image_identity.byte_length == 0
            || self.image_identity.byte_length > MAX_FINALIZER_IMAGE_BYTES
            || self.image_identity.link_count != 1
            || self.image_identity.attributes
                & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)
                != 0
            || is_zero(&self.image_sha256)
            || is_zero(&self.bootstrap_binding_sha256)
            || is_zero(&self.payload_set_binding_sha256)
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_elevated_lease_invalid",
            ));
        }
        self.exit_ready_tip.validate()?;
        if self.exit_ready_tip.stage() != FinalizerCommitStage::ExitReady {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_elevated_exit_tip_invalid",
            ));
        }
        store.verify_root_unchanged()?;
        let recovered = store.recover()?.ok_or(AuthorityMaintenanceError(
            "authority_finalizer_commit_store_receipt_missing",
        ))?;
        if recovered.protocol_state().binding() != self.binding
            || recovered.protocol_state().latest_stage() != expected_durable_stage
            || recovered_exit_ready_tip(&recovered)? != self.exit_ready_tip
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_elevated_lease_replay_mismatch",
            ));
        }
        match &self.backend {
            NativeElevatedFinalizerCommitLeaseBackend::Native { process, image } => {
                let (process_id, process_creation_time) = current_process_epoch(process)?;
                let security = process_security(process.as_raw_handle().cast())?;
                let (image_identity, image_sha256) = read_finalizer_image_stable(image)?;
                if process_id != self.process_id
                    || process_creation_time != self.process_creation_time
                    || image_identity != self.image_identity
                    || image_sha256 != self.image_sha256
                    || !security.elevated
                    || !security.high_integrity
                    || security.local_system
                    || security.session_id != self.session_id
                {
                    return Err(AuthorityMaintenanceError(
                        "authority_finalizer_commit_elevated_process_drift",
                    ));
                }
            }
            #[cfg(test)]
            NativeElevatedFinalizerCommitLeaseBackend::Fixture { actor_epoch } => {
                if *actor_epoch == 0 || *actor_epoch != self.process_creation_time {
                    return Err(AuthorityMaintenanceError(
                        "authority_finalizer_commit_elevated_test_epoch_invalid",
                    ));
                }
            }
        }
        store.verify_root_unchanged()
    }
}

impl FinalizerCommitReceiptStore {
    fn from_self_authenticated_root(
        root: AuthenticatedFinalizerCommitStoreRoot,
        active_head_transaction_sha256: [u8; 32],
    ) -> Result<Self, AuthorityMaintenanceError> {
        let binding =
            root.self_authenticate_transaction_started_binding(active_head_transaction_sha256)?;
        Self::from_authenticated_root(root, binding)
    }

    pub(super) fn from_authenticated_root(
        root: AuthenticatedFinalizerCommitStoreRoot,
        binding: FinalizerCommitBinding,
    ) -> Result<Self, AuthorityMaintenanceError> {
        verify_root_identity(&root.readback.identity)?;
        let current = (root.root_reverify)(&root.root)?;
        let namespace_current = (root.namespace_root_reverify)(&root.namespace_root)?;
        if !same_authenticated_root_readback(&current, &root.readback)
            || !same_authenticated_root_readback(&namespace_current, &root.namespace_readback)
            || !same_root_identity(&root.readback.identity, &root.namespace_readback.identity)
            || root.readback.canonical_path_readback_sha256
                != root.namespace_readback.canonical_path_readback_sha256
            || root.readback.complete_security_readback_sha256
                != root.namespace_readback.complete_security_readback_sha256
            || !same_root_identity(&native_file_identity(&root.root)?, &root.readback.identity)
            || root.readback.canonical_path_readback_sha256
                != canonical_path_sha256(&root.canonical_path)
            || is_zero(&root.readback.canonical_path_readback_sha256)
            || is_zero(&root.readback.complete_security_readback_sha256)
            || is_zero(&root.readback.granted_access_readback_sha256)
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_root_capability_invalid",
            ));
        }
        let authenticated_root_sha256 = authenticated_root_digest(&root.readback);
        if binding.final_commit_store_root_identity_sha256() != authenticated_root_sha256 {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_root_binding_mismatch",
            ));
        }
        let final_commit_gate_sha256 = binding.expected_final_commit_gate_sha256();
        Ok(Self {
            root: root.root,
            namespace_root: root.namespace_root,
            root_identity: root.readback.identity,
            namespace_root_readback: Some(root.namespace_readback),
            root_canonical_path: root.canonical_path,
            authenticated_root_sha256,
            final_commit_gate_sha256,
            root_reverify: Some(root.root_reverify),
            namespace_root_reverify: Some(root.namespace_root_reverify),
            receipt_reverify: Some(root.receipt_reverify),
            binding,
        })
    }

    pub(super) fn acquire_elevated_finalizer_lease(
        &self,
        capability: &VerifiedElevatedMaintenanceCapability,
        maintenance_lease: &VerifiedMaintenanceLease,
    ) -> Result<NativeElevatedFinalizerCommitLease, AuthorityMaintenanceError> {
        self.verify_root_unchanged()?;
        let recovered = self.recover()?.ok_or(AuthorityMaintenanceError(
            "authority_finalizer_commit_store_receipt_missing",
        ))?;
        if !matches!(
            recovered.protocol_state().latest_stage(),
            FinalizerCommitStage::ExitReady | FinalizerCommitStage::SealComplete
        ) || recovered.protocol_state().binding() != self.binding
            || !maintenance_lease.is_live()
            || capability.bootstrap_process_id != maintenance_lease.bootstrap_helper.process_id
            || capability.bootstrap_process_creation_time
                != maintenance_lease.bootstrap_helper.process_creation_time
            || capability.bootstrap_binding_sha256
                != maintenance_lease.bootstrap_helper.binding_sha256
            || capability.plan_sha256 != maintenance_lease.plan_sha256
            || capability.plan_sha256 != self.binding.plan_sha256()
            || capability.generation != maintenance_lease.generation
            || capability.generation != self.binding.generation_sha256()
            || capability.payload_set_binding_sha256 != maintenance_lease.payloads.binding_sha256
            || maintenance_lease.payloads.install_helper.descriptor
                != maintenance_lease.bootstrap_helper.image
            || maintenance_lease.payloads.install_helper.volume_serial
                != maintenance_lease.bootstrap_helper.image_volume_serial
            || maintenance_lease.payloads.install_helper.file_id
                != maintenance_lease.bootstrap_helper.image_file_id
            || !maintenance_lease.bootstrap_helper.elevated_token
            || !maintenance_lease.bootstrap_helper.high_integrity
            || maintenance_lease.bootstrap_helper.local_system
            || maintenance_lease.bootstrap_helper.session_id == 0
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_elevated_lease_binding_mismatch",
            ));
        }
        let exit_ready_tip = recovered_exit_ready_tip(&recovered)?;
        let native = match &maintenance_lease.held_payloads {
            HeldPayloadLease::Native(value) => value,
            #[cfg(test)]
            HeldPayloadLease::Test(_) => {
                return Err(AuthorityMaintenanceError(
                    "authority_finalizer_commit_elevated_native_handles_missing",
                ));
            }
        };
        let process = duplicate_current_process_handle(&native._bootstrap_process)?;
        let image = reopen_finalizer_image_handle(&native._bootstrap_running_image)?;
        let (process_id, process_creation_time) = current_process_epoch(&process)?;
        let security = process_security(process.as_raw_handle().cast())?;
        let (image_identity, image_sha256) = read_finalizer_image_stable(&image)?;
        let expected_image = maintenance_lease.bootstrap_helper.image;
        if process_id != capability.bootstrap_process_id
            || process_creation_time != capability.bootstrap_process_creation_time
            || image_identity.volume_serial
                != maintenance_lease.bootstrap_helper.image_volume_serial
            || image_identity.file_id != maintenance_lease.bootstrap_helper.image_file_id
            || image_identity.volume_serial
                != maintenance_lease.payloads.install_helper.volume_serial
            || image_identity.file_id != maintenance_lease.payloads.install_helper.file_id
            || image_identity.byte_length != expected_image.byte_length()
            || image_sha256 != *expected_image.sha256()
            || !security.elevated
            || !security.high_integrity
            || security.local_system
            || security.session_id == 0
            || security.session_id != maintenance_lease.bootstrap_helper.session_id
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_elevated_process_mismatch",
            ));
        }
        let value = NativeElevatedFinalizerCommitLease {
            binding: self.binding,
            authenticated_root_sha256: self.authenticated_root_sha256,
            exit_ready_tip,
            process_id,
            process_creation_time,
            session_id: security.session_id,
            image_identity,
            image_sha256,
            bootstrap_binding_sha256: capability.bootstrap_binding_sha256,
            payload_set_binding_sha256: capability.payload_set_binding_sha256,
            backend: NativeElevatedFinalizerCommitLeaseBackend::Native { process, image },
        };
        value.revalidate(self, recovered.protocol_state().latest_stage())?;
        Ok(value)
    }

    #[cfg(test)]
    pub(super) fn acquire_elevated_finalizer_lease_for_test(
        &self,
        actor_epoch: u64,
    ) -> Result<NativeElevatedFinalizerCommitLease, AuthorityMaintenanceError> {
        if actor_epoch == 0 {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_elevated_test_epoch_invalid",
            ));
        }
        let recovered = self.recover()?.ok_or(AuthorityMaintenanceError(
            "authority_finalizer_commit_store_receipt_missing",
        ))?;
        if !matches!(
            recovered.protocol_state().latest_stage(),
            FinalizerCommitStage::ExitReady | FinalizerCommitStage::SealComplete
        ) {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_elevated_lease_stage_invalid",
            ));
        }
        let mut image_file_id = [0u8; 16];
        image_file_id[..8].copy_from_slice(&actor_epoch.to_be_bytes());
        image_file_id[8..].copy_from_slice(&actor_epoch.rotate_left(17).to_be_bytes());
        let actor_digest: [u8; 32] = Sha256::digest(actor_epoch.to_be_bytes()).into();
        let payload_digest: [u8; 32] =
            Sha256::digest(actor_epoch.wrapping_add(1).to_be_bytes()).into();
        let value = NativeElevatedFinalizerCommitLease {
            binding: self.binding,
            authenticated_root_sha256: self.authenticated_root_sha256,
            exit_ready_tip: recovered_exit_ready_tip(&recovered)?,
            process_id: (actor_epoch as u32).max(1),
            process_creation_time: actor_epoch,
            session_id: 1,
            image_identity: NativeFileIdentity {
                volume_serial: actor_epoch,
                file_id: image_file_id,
                byte_length: actor_epoch,
                link_count: 1,
                attributes: FILE_ATTRIBUTE_NORMAL,
            },
            image_sha256: actor_digest,
            bootstrap_binding_sha256: actor_digest,
            payload_set_binding_sha256: payload_digest,
            backend: NativeElevatedFinalizerCommitLeaseBackend::Fixture { actor_epoch },
        };
        value.revalidate(self, recovered.protocol_state().latest_stage())?;
        Ok(value)
    }

    #[cfg(test)]
    pub(super) fn open_unsecured_test(
        root_path: &Path,
        binding: FinalizerCommitBinding,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let root = open_root_for_test(root_path)?;
        let namespace_root = open_root_for_test(root_path)?;
        let root_identity = native_file_identity(&root)?;
        verify_root_identity(&root_identity)?;
        let authenticated_root_sha256 = test_root_digest(&root_identity);
        let root_canonical_path = canonical_handle_path(&root)?;
        if binding.final_commit_store_root_identity_sha256() != authenticated_root_sha256 {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_root_binding_mismatch",
            ));
        }
        let final_commit_gate_sha256 = binding.expected_final_commit_gate_sha256();
        Ok(Self {
            root,
            namespace_root,
            root_identity,
            namespace_root_readback: None,
            root_canonical_path,
            authenticated_root_sha256,
            final_commit_gate_sha256,
            root_reverify: None,
            namespace_root_reverify: None,
            receipt_reverify: None,
            binding,
        })
    }

    #[cfg(test)]
    pub(super) fn unsecured_test_root_identity_sha256(
        root_path: &Path,
    ) -> Result<[u8; 32], AuthorityMaintenanceError> {
        let root = open_root_for_test(root_path)?;
        let identity = native_file_identity(&root)?;
        verify_root_identity(&identity)?;
        Ok(test_root_digest(&identity))
    }

    pub(super) fn persist_transaction_started(
        &self,
        state: &FinalizerCommitProtocolState,
    ) -> Result<PersistedFinalizerCommitStage, AuthorityMaintenanceError> {
        if state.latest_stage() != FinalizerCommitStage::TransactionStarted {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_stage_invalid",
            ));
        }
        self.persist_state(state, None)
    }

    pub(super) fn persist_system_transition(
        &self,
        state: &FinalizerCommitProtocolState,
        write: &DurableReceiptWrite,
    ) -> Result<PersistedFinalizerCommitStage, AuthorityMaintenanceError> {
        let stage = state.latest_stage();
        if !matches!(
            stage,
            FinalizerCommitStage::ApplyReady
                | FinalizerCommitStage::SealReady
                | FinalizerCommitStage::ExitReady
        ) {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_transition_actor_invalid",
            ));
        }
        self.validate_transition_write(state, write)?;
        self.persist_state(state, Some(write))
    }

    pub(super) fn persist_seal_complete_from_elevated_finalizer(
        &self,
        state: &FinalizerCommitProtocolState,
        write: &DurableReceiptWrite,
        generation_authorization: &GenerationSealTerminalAuthorization,
        finalizer_lease: &NativeElevatedFinalizerCommitLease,
    ) -> Result<PersistedFinalizerCommitStage, AuthorityMaintenanceError> {
        let expected_durable_stage = match write.disposition() {
            ProtocolWriteDisposition::Created => FinalizerCommitStage::ExitReady,
            ProtocolWriteDisposition::AlreadyIdentical => FinalizerCommitStage::SealComplete,
        };
        finalizer_lease.revalidate(self, expected_durable_stage)?;
        let authorization =
            SealCompletePersistenceAuthorization::from_generation_seal_authorization(
                generation_authorization,
            )?;
        self.persist_seal_complete(state, write, &authorization)
    }

    fn persist_seal_complete(
        &self,
        state: &FinalizerCommitProtocolState,
        write: &DurableReceiptWrite,
        authorization: &SealCompletePersistenceAuthorization,
    ) -> Result<PersistedFinalizerCommitStage, AuthorityMaintenanceError> {
        if state.latest_stage() != FinalizerCommitStage::SealComplete {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_seal_complete_stage_invalid",
            ));
        }
        self.validate_transition_write(state, write)?;
        authorization.validate(self.binding, state)?;
        self.persist_state(state, Some(write))
    }

    pub(super) fn persist_final_commit_from_elevated_finalizer(
        &self,
        state: &FinalizerCommitProtocolState,
        write: &DurableReceiptWrite,
        finalizer_lease: &NativeElevatedFinalizerCommitLease,
    ) -> Result<PersistedFinalizerCommitStage, AuthorityMaintenanceError> {
        let expected_durable_stage = match write.disposition() {
            ProtocolWriteDisposition::Created => FinalizerCommitStage::SealComplete,
            ProtocolWriteDisposition::AlreadyIdentical => FinalizerCommitStage::FinalCommit,
        };
        finalizer_lease.revalidate(self, expected_durable_stage)?;
        let scanned = self.scan()?;
        let seal_complete_tip = scanned
            .get(stage_index(FinalizerCommitStage::SealComplete))
            .ok_or(AuthorityMaintenanceError(
                "authority_finalizer_final_commit_seal_complete_missing",
            ))?
            .reference;
        let authorization = FinalCommitPersistenceAuthorization::from_elevated_finalizer_lease(
            self,
            state,
            seal_complete_tip,
            finalizer_lease,
            expected_durable_stage,
        )?;
        self.persist_final_commit(state, write, &authorization)
    }

    fn persist_final_commit(
        &self,
        state: &FinalizerCommitProtocolState,
        write: &DurableReceiptWrite,
        authorization: &FinalCommitPersistenceAuthorization,
    ) -> Result<PersistedFinalizerCommitStage, AuthorityMaintenanceError> {
        if state.latest_stage() != FinalizerCommitStage::FinalCommit {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_final_commit_stage_invalid",
            ));
        }
        self.validate_transition_write(state, write)?;
        let scanned = self.scan()?;
        let seal_complete_tip = scanned
            .get(stage_index(FinalizerCommitStage::SealComplete))
            .ok_or(AuthorityMaintenanceError(
                "authority_finalizer_final_commit_seal_complete_missing",
            ))?
            .reference;
        authorization.validate(
            self.binding,
            self.authenticated_root_sha256,
            self.final_commit_gate_sha256,
            seal_complete_tip,
            state,
        )?;
        self.persist_state(state, Some(write))
    }

    fn validate_transition_write(
        &self,
        state: &FinalizerCommitProtocolState,
        write: &DurableReceiptWrite,
    ) -> Result<(), AuthorityMaintenanceError> {
        let stage = state.latest_stage();
        if stage == FinalizerCommitStage::TransactionStarted
            || write.stage() != stage
            || state.receipt_sha256(stage)? != Some(write.receipt_sha256())
            || write.canonical_json().is_empty()
            || write.canonical_json().len() > MAX_FINALIZER_COMMIT_RECEIPT_BYTES
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_transition_mismatch",
            ));
        }
        Ok(())
    }

    /// Legacy two-object diagnostics retained only for regression tests. They
    /// are never consulted by production recovery or privileged publication.
    #[cfg(test)]
    fn persist_seal_intent(
        &self,
        evidence: FinalizerSealIntentEvidence,
    ) -> Result<PersistedSealAuxiliaryReference, AuthorityMaintenanceError> {
        let recovered = self.recover()?.ok_or(AuthorityMaintenanceError(
            "authority_finalizer_commit_store_receipt_missing",
        ))?;
        if recovered.protocol_state.latest_stage() != FinalizerCommitStage::ExitReady {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_seal_intent_stage_invalid",
            ));
        }
        let value = DurableFinalizerSealIntent::new(
            self.binding,
            self.authenticated_root_sha256,
            recovered.tip(),
            evidence,
        )?;
        self.persist_auxiliary(SEAL_INTENT_FILE_NAME, &value.canonical_json()?)
    }

    #[cfg(test)]
    fn persist_seal_progress(
        &self,
        evidence: FinalizerSealProgressEvidence,
    ) -> Result<PersistedSealAuxiliaryReference, AuthorityMaintenanceError> {
        let scanned = self.scan()?;
        if scanned.last().map(|value| value.envelope.stage) != Some(FinalizerCommitStage::ExitReady)
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_seal_progress_stage_invalid",
            ));
        }
        let exit_ready = scanned
            .get(stage_index(FinalizerCommitStage::ExitReady))
            .ok_or(AuthorityMaintenanceError(
                "authority_finalizer_seal_intent_stage_invalid",
            ))?
            .reference;
        let seal = self.recover_seal_progress(exit_ready)?;
        let intent_file = seal.intent.ok_or(AuthorityMaintenanceError(
            "authority_finalizer_seal_intent_missing",
        ))?;
        let intent = self
            .read_seal_intent(exit_ready)?
            .ok_or(AuthorityMaintenanceError(
                "authority_finalizer_seal_intent_missing",
            ))?;
        let value = DurableFinalizerSealProgress::new(
            self.binding,
            self.authenticated_root_sha256,
            intent_file,
            evidence,
        )?;
        value.validate_against_intent(evidence.artifact, &intent.value.evidence)?;
        let name = match evidence.artifact {
            FinalizerSealArtifact::WorkerNonce => WORKER_SEAL_PROGRESS_FILE_NAME,
            FinalizerSealArtifact::CandidateConsumption => CANDIDATE_SEAL_PROGRESS_FILE_NAME,
        };
        self.persist_auxiliary(name, &value.canonical_json()?)
    }

    pub(super) fn recover(
        &self,
    ) -> Result<Option<RecoveredFinalizerCommitState>, AuthorityMaintenanceError> {
        let scanned = self.scan()?;
        let Some(latest) = scanned.last() else {
            return Ok(None);
        };
        let latest_stage = latest.envelope.stage;
        if latest_stage == FinalizerCommitStage::FinalCommit
            && latest
                .envelope
                .protocol_state
                .final_commit_persistence_projection()?
                .is_none()
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_final_commit_persistence_projection_missing",
            ));
        }
        Ok(Some(RecoveredFinalizerCommitState {
            protocol_state: latest.envelope.protocol_state.as_ref().clone(),
            files: scanned.iter().map(|value| value.reference).collect(),
            security: security_expectation(latest_stage),
            directive: recovery_directive(latest_stage),
        }))
    }

    pub(super) fn recover_exact_tip(
        &self,
        expected_tip: PersistedReceiptFileReference,
    ) -> Result<RecoveredFinalizerCommitState, AuthorityMaintenanceError> {
        let recovered = self.recover()?.ok_or(AuthorityMaintenanceError(
            "authority_finalizer_commit_store_receipt_missing",
        ))?;
        if recovered.tip() != expected_tip {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_tip_identity_mismatch",
            ));
        }
        Ok(recovered)
    }

    fn recover_typed_final_commit(
        &self,
        active_head_transaction_sha256: [u8; 32],
    ) -> Result<RecoveredFinalCommitReadback, AuthorityMaintenanceError> {
        let recovered = self.recover()?.ok_or(AuthorityMaintenanceError(
            "authority_finalizer_commit_store_receipt_missing",
        ))?;
        if recovered.directive()
            != FinalizerCommitRecoveryDirective::ReadOnlyVerifyFinalCommitAndRuntime
            || recovered.protocol_state().latest_stage() != FinalizerCommitStage::FinalCommit
            || recovered.files().len() != STAGES.len()
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_terminal_readback_incomplete",
            ));
        }
        let tip = recovered.tip();
        let projection = recovered
            .protocol_state()
            .final_commit_persistence_projection()?
            .ok_or(AuthorityMaintenanceError(
                "authority_finalizer_final_commit_persistence_projection_missing",
            ))?;
        let binding = projection.binding();
        if tip.stage() != FinalizerCommitStage::FinalCommit
            || binding != self.binding
            || binding.transaction_sha256() != active_head_transaction_sha256
            || binding.final_commit_store_root_identity_sha256() != self.authenticated_root_sha256
            || projection.expected_final_commit_gate_sha256() != self.final_commit_gate_sha256
            || projection.final_commit_receipt_sha256() != tip.receipt_sha256()
            || projection.protocol_state_sha256() != tip.protocol_state_sha256()
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_terminal_readback_binding_mismatch",
            ));
        }
        Ok(RecoveredFinalCommitReadback {
            active_head_transaction_sha256,
            authenticated_root_sha256: self.authenticated_root_sha256,
            tip,
            projection,
        })
    }

    fn persist_state(
        &self,
        state: &FinalizerCommitProtocolState,
        _write: Option<&DurableReceiptWrite>,
    ) -> Result<PersistedFinalizerCommitStage, AuthorityMaintenanceError> {
        self.verify_root_unchanged()?;
        if state.binding() != self.binding {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_binding_mismatch",
            ));
        }
        let scanned = self.scan()?;
        let namespace = self.typed_transaction_namespace_inventory()?;
        self.validate_namespace_for_protocol_state(Some(state), &namespace)?;
        let stage = state.latest_stage();
        let stage_position = stage_index(stage);
        let previous = if stage_position == 0 {
            None
        } else {
            scanned.get(stage_position - 1).map(|value| value.reference)
        };
        let envelope =
            DurableFinalizerCommitEnvelope::new(state, self.authenticated_root_sha256, previous)?;
        let bytes = envelope.canonical_json()?;

        if let Some(existing) = scanned.get(stage_position) {
            if existing.envelope != envelope || existing.bytes != bytes {
                return Err(AuthorityMaintenanceError(
                    "authority_finalizer_commit_store_receipt_conflict",
                ));
            }
            return Ok(PersistedFinalizerCommitStage {
                disposition: ProtocolWriteDisposition::AlreadyIdentical,
                file: existing.reference,
            });
        }

        if scanned.len() != stage_position {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_chain_gap",
            ));
        }
        let name = stage_file_name(stage);
        match publish_relative_atomic(
            &self.root,
            &self.root_canonical_path,
            name,
            &bytes,
            self.receipt_reverify,
        )? {
            CreateResult::Created(writer_identity) => {
                let reopened =
                    self.read_stage(stage, previous.as_ref())?
                        .ok_or(AuthorityMaintenanceError(
                            "authority_finalizer_commit_store_readonly_reopen_failed",
                        ))?;
                if reopened.native_identity != writer_identity
                    || reopened.envelope != envelope
                    || reopened.bytes != bytes
                {
                    return Err(AuthorityMaintenanceError(
                        "authority_finalizer_commit_store_readonly_reopen_mismatch",
                    ));
                }
                self.verify_root_unchanged()?;
                Ok(PersistedFinalizerCommitStage {
                    disposition: ProtocolWriteDisposition::Created,
                    file: reopened.reference,
                })
            }
            CreateResult::Collision => {
                let reopened =
                    self.read_stage(stage, previous.as_ref())?
                        .ok_or(AuthorityMaintenanceError(
                            "authority_finalizer_commit_store_name_reuse_mismatch",
                        ))?;
                if reopened.envelope != envelope || reopened.bytes != bytes {
                    return Err(AuthorityMaintenanceError(
                        "authority_finalizer_commit_store_name_reuse_mismatch",
                    ));
                }
                Ok(PersistedFinalizerCommitStage {
                    disposition: ProtocolWriteDisposition::AlreadyIdentical,
                    file: reopened.reference,
                })
            }
        }
    }

    #[cfg(test)]
    fn persist_auxiliary(
        &self,
        name: &str,
        bytes: &[u8],
    ) -> Result<PersistedSealAuxiliaryReference, AuthorityMaintenanceError> {
        if self.final_commit_name_exists()? {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_post_commit_mutation_forbidden",
            ));
        }
        match publish_relative_atomic(
            &self.root,
            &self.root_canonical_path,
            name,
            bytes,
            self.receipt_reverify,
        )? {
            CreateResult::Created(_) | CreateResult::Collision => {}
        }
        let file = self.read_auxiliary(name)?.ok_or(AuthorityMaintenanceError(
            "authority_finalizer_seal_auxiliary_missing",
        ))?;
        if file.bytes != bytes {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_seal_auxiliary_conflict",
            ));
        }
        Ok(file.reference)
    }

    fn typed_transaction_namespace_inventory(
        &self,
    ) -> Result<TypedTransactionNamespaceInventory, AuthorityMaintenanceError> {
        typed_transaction_namespace_inventory_from_handle(&self.namespace_root)
    }

    fn validate_namespace_for_protocol_state(
        &self,
        state: Option<&FinalizerCommitProtocolState>,
        inventory: &TypedTransactionNamespaceInventory,
    ) -> Result<(), AuthorityMaintenanceError> {
        validate_typed_namespace_for_protocol_state(state, inventory)
    }

    fn scan(&self) -> Result<Vec<ScannedStage>, AuthorityMaintenanceError> {
        self.verify_root_unchanged()?;
        let initial_namespace = self.typed_transaction_namespace_inventory()?;
        let terminal_read_only = self.final_commit_name_exists()?;
        if terminal_read_only && initial_namespace.private_member_count != 0 {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_post_commit_publishing_residue",
            ));
        }
        if terminal_read_only {
            for legacy_name in [
                SEAL_INTENT_FILE_NAME,
                WORKER_SEAL_PROGRESS_FILE_NAME,
                CANDIDATE_SEAL_PROGRESS_FILE_NAME,
            ] {
                if relative_exists(
                    &self.root,
                    &publishing_name(legacy_name),
                    self.receipt_reverify,
                )? {
                    return Err(AuthorityMaintenanceError(
                        "authority_finalizer_commit_store_post_commit_publishing_residue",
                    ));
                }
            }
        }
        let mut result = Vec::with_capacity(STAGES.len());
        let mut gap_seen = false;
        for stage in STAGES {
            let previous = result.last().map(|value: &ScannedStage| &value.reference);
            let publishing_exists = relative_exists(
                &self.root,
                &publishing_name(stage_file_name(stage)),
                self.receipt_reverify,
            )?;
            if terminal_read_only && publishing_exists {
                return Err(AuthorityMaintenanceError(
                    "authority_finalizer_commit_store_post_commit_publishing_residue",
                ));
            }
            if gap_seen {
                if relative_exists(&self.root, stage_file_name(stage), self.receipt_reverify)?
                    || publishing_exists
                {
                    return Err(AuthorityMaintenanceError(
                        "authority_finalizer_commit_store_chain_gap",
                    ));
                }
                continue;
            }
            if !terminal_read_only {
                self.recover_stage_publication(stage, previous)?;
            }
            self.recover_authenticated_final_receipt_security(stage, previous)?;
            match self.read_stage(stage, previous)? {
                Some(value) => {
                    result.push(value);
                }
                None => gap_seen = true,
            }
        }
        let final_namespace = self.typed_transaction_namespace_inventory()?;
        self.validate_namespace_for_protocol_state(
            result
                .last()
                .map(|value| value.envelope.protocol_state.as_ref()),
            &final_namespace,
        )?;
        self.verify_root_unchanged()?;
        Ok(result)
    }

    fn recover_authenticated_final_receipt_security(
        &self,
        stage: FinalizerCommitStage,
        previous: Option<&PersistedReceiptFileReference>,
    ) -> Result<(), AuthorityMaintenanceError> {
        let Some(receipt_reverify) = self.receipt_reverify else {
            return Ok(());
        };
        let final_name = stage_file_name(stage);
        let final_path = self.receipt_canonical_path(final_name)?;
        let Some(inspection) =
            open_relative_optional(&self.root, final_name, AUTHENTICATED_RECEIPT_READ_ACCESS)?
        else {
            return Ok(());
        };
        let security_phase =
            classify_final_receipt_security(privileged_complete_security_sha256(&inspection)?)?;
        let inspection_kind = match security_phase {
            FinalReceiptSecurityPhase::PrivateSealed => {
                FinalizerCommitReceiptHandleKind::PublishingInspectionSealed
            }
            FinalReceiptSecurityPhase::PublishedImmutable => {
                FinalizerCommitReceiptHandleKind::PublishedReadOnly
            }
        };
        let inspection_capability = verify_receipt_capability(
            &inspection,
            inspection_kind,
            Some(receipt_reverify),
            &final_path,
        )?;
        let (inspection_bytes, inspection_identity, _) = read_bounded_stable(&inspection)?;
        DurableFinalizerCommitEnvelope::parse_canonical(
            &inspection_bytes,
            self.binding,
            self.authenticated_root_sha256,
            stage,
            previous,
        )?;
        require_same_receipt_capability(
            &inspection,
            inspection_kind,
            Some(receipt_reverify),
            inspection_capability,
            &final_path,
        )?;
        if security_phase == FinalReceiptSecurityPhase::PublishedImmutable {
            return Ok(());
        }
        drop(inspection);

        let sealed = open_relative_optional(
            &self.root,
            final_name,
            AUTHENTICATED_RECEIPT_SEALED_RECOVERY_ACCESS,
        )?
        .ok_or(AuthorityMaintenanceError(
            "authority_finalizer_commit_store_final_private_receipt_missing",
        ))?;
        let sealed_capability = verify_receipt_capability(
            &sealed,
            FinalizerCommitReceiptHandleKind::PublishingRecoverySealed,
            Some(receipt_reverify),
            &final_path,
        )?;
        let (sealed_bytes, sealed_identity, _) = read_bounded_stable(&sealed)?;
        if sealed_identity != inspection_identity || sealed_bytes != inspection_bytes {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_final_private_identity_drift",
            ));
        }
        DurableFinalizerCommitEnvelope::parse_canonical(
            &sealed_bytes,
            self.binding,
            self.authenticated_root_sha256,
            stage,
            previous,
        )?;
        require_same_receipt_capability(
            &sealed,
            FinalizerCommitReceiptHandleKind::PublishingRecoverySealed,
            Some(receipt_reverify),
            sealed_capability,
            &final_path,
        )?;
        transition_publication_security(
            &sealed,
            FinalizerPublicationSecurityPhase::PrivateSealed,
            FinalizerPublicationSecurityPhase::PublishedImmutable,
        )
        .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        flush_handle(
            &sealed,
            "authority_finalizer_commit_store_final_private_tighten_flush_failed",
        )?;
        verify_receipt_capability(
            &sealed,
            FinalizerCommitReceiptHandleKind::PublishedTightening,
            Some(receipt_reverify),
            &final_path,
        )?;
        if native_file_identity(&sealed)? != inspection_identity {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_final_private_identity_drift",
            ));
        }
        drop(sealed);
        flush_handle(
            &self.root,
            "authority_finalizer_commit_store_parent_flush_failed",
        )?;

        let published =
            open_relative_optional(&self.root, final_name, AUTHENTICATED_RECEIPT_READ_ACCESS)?
                .ok_or(AuthorityMaintenanceError(
                    "authority_finalizer_commit_store_published_receipt_missing",
                ))?;
        let published_capability = verify_receipt_capability(
            &published,
            FinalizerCommitReceiptHandleKind::PublishedReadOnly,
            Some(receipt_reverify),
            &final_path,
        )?;
        let (published_bytes, published_identity, _) = read_bounded_stable(&published)?;
        if published_identity != inspection_identity || published_bytes != inspection_bytes {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_final_private_identity_drift",
            ));
        }
        DurableFinalizerCommitEnvelope::parse_canonical(
            &published_bytes,
            self.binding,
            self.authenticated_root_sha256,
            stage,
            previous,
        )?;
        require_same_receipt_capability(
            &published,
            FinalizerCommitReceiptHandleKind::PublishedReadOnly,
            Some(receipt_reverify),
            published_capability,
            &final_path,
        )
    }

    fn read_stage(
        &self,
        stage: FinalizerCommitStage,
        previous: Option<&PersistedReceiptFileReference>,
    ) -> Result<Option<ScannedStage>, AuthorityMaintenanceError> {
        let expected_path = self.receipt_canonical_path(stage_file_name(stage))?;
        let Some(file) = open_relative_readonly_optional(
            &self.root,
            stage_file_name(stage),
            self.receipt_reverify,
        )?
        else {
            return Ok(None);
        };
        let initial_capability_sha256 = verify_receipt_capability(
            &file,
            FinalizerCommitReceiptHandleKind::PublishedReadOnly,
            self.receipt_reverify,
            &expected_path,
        )?;
        let (bytes, native_identity, bytes_sha256) = read_bounded_stable(&file)?;
        let envelope = DurableFinalizerCommitEnvelope::parse_canonical(
            &bytes,
            self.binding,
            self.authenticated_root_sha256,
            stage,
            previous,
        )?;
        let final_capability_sha256 = verify_receipt_capability(
            &file,
            FinalizerCommitReceiptHandleKind::PublishedReadOnly,
            self.receipt_reverify,
            &expected_path,
        )?;
        if final_capability_sha256 != initial_capability_sha256 {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_receipt_authentication_drift",
            ));
        }
        let identity = DurableFileIdentity::new(
            native_identity.volume_serial,
            native_identity.file_id,
            native_identity.link_count,
            native_identity.byte_length,
            bytes_sha256,
        )?;
        let security_readback_sha256 =
            published_receipt_reference_security_sha256(&native_identity, &expected_path)?;
        let reference = PersistedReceiptFileReference::new(
            stage,
            identity,
            envelope.receipt_sha256,
            envelope.protocol_state_sha256,
            security_readback_sha256,
        )?;
        Ok(Some(ScannedStage {
            envelope,
            bytes,
            native_identity,
            reference,
            _held_read_only: file,
        }))
    }

    #[cfg(test)]
    fn recover_seal_progress(
        &self,
        exit_ready_file: PersistedReceiptFileReference,
    ) -> Result<RecoveredSealProgress, AuthorityMaintenanceError> {
        let Some(intent) = self.read_seal_intent(exit_ready_file)? else {
            for name in [
                WORKER_SEAL_PROGRESS_FILE_NAME,
                CANDIDATE_SEAL_PROGRESS_FILE_NAME,
            ] {
                if relative_exists(&self.root, name, self.receipt_reverify)?
                    || relative_exists(&self.root, &publishing_name(name), self.receipt_reverify)?
                {
                    return Err(AuthorityMaintenanceError(
                        "authority_finalizer_seal_progress_without_intent",
                    ));
                }
            }
            return Ok(RecoveredSealProgress::default());
        };
        let intent_reference = intent.reference;
        let worker = self.read_seal_progress(
            WORKER_SEAL_PROGRESS_FILE_NAME,
            FinalizerSealArtifact::WorkerNonce,
            intent_reference,
            &intent.value.evidence,
        )?;
        let candidate = self.read_seal_progress(
            CANDIDATE_SEAL_PROGRESS_FILE_NAME,
            FinalizerSealArtifact::CandidateConsumption,
            intent_reference,
            &intent.value.evidence,
        )?;
        let worker_verified = worker
            .as_ref()
            .is_some_and(|value| value.value.evidence.is_exact());
        let candidate_verified = candidate
            .as_ref()
            .is_some_and(|value| value.value.evidence.is_exact());
        Ok(RecoveredSealProgress {
            intent: Some(intent_reference),
            worker: worker.map(|value| value.reference),
            candidate: candidate.map(|value| value.reference),
            worker_verified,
            candidate_verified,
        })
    }

    #[cfg(test)]
    fn read_seal_intent(
        &self,
        exit_ready_file: PersistedReceiptFileReference,
    ) -> Result<Option<ScannedSealIntent>, AuthorityMaintenanceError> {
        self.recover_auxiliary_publication(SEAL_INTENT_FILE_NAME, |bytes| {
            DurableFinalizerSealIntent::parse_canonical(
                bytes,
                self.binding,
                self.authenticated_root_sha256,
                exit_ready_file,
            )
            .map(|_| ())
        })?;
        let Some(file) = self.read_auxiliary(SEAL_INTENT_FILE_NAME)? else {
            return Ok(None);
        };
        let value = DurableFinalizerSealIntent::parse_canonical(
            &file.bytes,
            self.binding,
            self.authenticated_root_sha256,
            exit_ready_file,
        )?;
        Ok(Some(ScannedSealIntent {
            value,
            reference: file.reference,
        }))
    }

    #[cfg(test)]
    fn read_seal_progress(
        &self,
        name: &str,
        artifact: FinalizerSealArtifact,
        intent_file: PersistedSealAuxiliaryReference,
        intent: &FinalizerSealIntentEvidence,
    ) -> Result<Option<ScannedSealProgress>, AuthorityMaintenanceError> {
        self.recover_auxiliary_publication(name, |bytes| {
            DurableFinalizerSealProgress::parse_canonical(
                bytes,
                self.binding,
                self.authenticated_root_sha256,
                intent_file,
                artifact,
                intent,
            )
            .map(|_| ())
        })?;
        let Some(file) = self.read_auxiliary(name)? else {
            return Ok(None);
        };
        let value = DurableFinalizerSealProgress::parse_canonical(
            &file.bytes,
            self.binding,
            self.authenticated_root_sha256,
            intent_file,
            artifact,
            intent,
        )?;
        Ok(Some(ScannedSealProgress {
            value,
            reference: file.reference,
        }))
    }

    #[cfg(test)]
    fn read_auxiliary(
        &self,
        name: &str,
    ) -> Result<Option<ScannedAuxiliary>, AuthorityMaintenanceError> {
        let expected_path = self.receipt_canonical_path(name)?;
        let Some(file) = open_relative_readonly_optional(&self.root, name, self.receipt_reverify)?
        else {
            return Ok(None);
        };
        let initial_security_readback_sha256 = verify_receipt_capability(
            &file,
            FinalizerCommitReceiptHandleKind::PublishedReadOnly,
            self.receipt_reverify,
            &expected_path,
        )?;
        let (bytes, identity, bytes_sha256) = read_bounded_stable(&file)?;
        let security_readback_sha256 = verify_receipt_capability(
            &file,
            FinalizerCommitReceiptHandleKind::PublishedReadOnly,
            self.receipt_reverify,
            &expected_path,
        )?;
        if security_readback_sha256 != initial_security_readback_sha256 {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_receipt_authentication_drift",
            ));
        }
        let identity = DurableFileIdentity::new(
            identity.volume_serial,
            identity.file_id,
            identity.link_count,
            identity.byte_length,
            bytes_sha256,
        )?;
        Ok(Some(ScannedAuxiliary {
            bytes,
            reference: PersistedSealAuxiliaryReference::new(identity, security_readback_sha256)?,
        }))
    }

    #[cfg(test)]
    fn recover_auxiliary_publication<F>(
        &self,
        name: &str,
        validate: F,
    ) -> Result<(), AuthorityMaintenanceError>
    where
        F: Fn(&[u8]) -> Result<(), AuthorityMaintenanceError>,
    {
        let publishing_name = publishing_name(name);
        let publishing_path = self.receipt_canonical_path(&publishing_name)?;
        if self.final_commit_name_exists()? {
            if relative_exists(&self.root, &publishing_name, self.receipt_reverify)? {
                return Err(AuthorityMaintenanceError(
                    "authority_finalizer_commit_store_post_commit_publishing_residue",
                ));
            }
            return Ok(());
        }
        let final_exists = relative_exists(&self.root, name, self.receipt_reverify)?;
        let Some(publishing) = open_relative_optional(
            &self.root,
            &publishing_name,
            receipt_create_access(self.receipt_reverify),
        )?
        else {
            return Ok(());
        };
        let identity = native_file_identity(&publishing)?;
        verify_publishable_identity(&identity)?;
        let initial_security_readback_sha256 = verify_receipt_capability(
            &publishing,
            FinalizerCommitReceiptHandleKind::PublishingStagingRecovery,
            self.receipt_reverify,
            &publishing_path,
        )?;
        if final_exists {
            require_same_receipt_capability(
                &publishing,
                FinalizerCommitReceiptHandleKind::PublishingStagingRecovery,
                self.receipt_reverify,
                initial_security_readback_sha256,
                &publishing_path,
            )?;
            return delete_publishing(&self.root, publishing);
        }
        if identity.byte_length == 0
            || identity.byte_length > MAX_FINALIZER_COMMIT_RECEIPT_BYTES as u64
        {
            require_same_receipt_capability(
                &publishing,
                FinalizerCommitReceiptHandleKind::PublishingStagingRecovery,
                self.receipt_reverify,
                initial_security_readback_sha256,
                &publishing_path,
            )?;
            return delete_publishing(&self.root, publishing);
        }
        let (bytes, stable_identity, _) = read_bounded_stable(&publishing)?;
        if validate(&bytes).is_err() {
            require_same_receipt_capability(
                &publishing,
                FinalizerCommitReceiptHandleKind::PublishingStagingRecovery,
                self.receipt_reverify,
                initial_security_readback_sha256,
                &publishing_path,
            )?;
            return delete_publishing(&self.root, publishing);
        }
        flush_handle(
            &publishing,
            "authority_finalizer_commit_store_publishing_flush_failed",
        )?;
        require_same_receipt_capability(
            &publishing,
            FinalizerCommitReceiptHandleKind::PublishingStagingRecovery,
            self.receipt_reverify,
            initial_security_readback_sha256,
            &publishing_path,
        )?;
        match rename_relative_no_replace(&publishing, &self.root, name)? {
            RenameDisposition::Published => {
                if native_file_identity(&publishing)? != stable_identity {
                    return Err(AuthorityMaintenanceError(
                        "authority_finalizer_commit_store_publishing_identity_drift",
                    ));
                }
                drop(publishing);
                flush_handle(
                    &self.root,
                    "authority_finalizer_commit_store_parent_flush_failed",
                )
            }
            RenameDisposition::DestinationExists => {
                require_same_receipt_capability(
                    &publishing,
                    FinalizerCommitReceiptHandleKind::PublishingStagingRecovery,
                    self.receipt_reverify,
                    initial_security_readback_sha256,
                    &publishing_path,
                )?;
                delete_publishing(&self.root, publishing)
            }
        }
    }

    fn recover_stage_publication(
        &self,
        stage: FinalizerCommitStage,
        previous: Option<&PersistedReceiptFileReference>,
    ) -> Result<(), AuthorityMaintenanceError> {
        if self.receipt_reverify.is_some() {
            return self.recover_authenticated_stage_publication(stage, previous);
        }
        let final_name = stage_file_name(stage);
        let publishing_name = publishing_name(final_name);
        let publishing_path = self.receipt_canonical_path(&publishing_name)?;
        let final_exists = relative_exists(&self.root, final_name, self.receipt_reverify)?;
        let Some(publishing) = open_relative_optional(
            &self.root,
            &publishing_name,
            receipt_create_access(self.receipt_reverify),
        )?
        else {
            return Ok(());
        };
        let identity = native_file_identity(&publishing)?;
        verify_publishable_identity(&identity)?;
        let initial_security_readback_sha256 = verify_receipt_capability(
            &publishing,
            FinalizerCommitReceiptHandleKind::PublishingStagingRecovery,
            self.receipt_reverify,
            &publishing_path,
        )?;
        if final_exists {
            require_same_receipt_capability(
                &publishing,
                FinalizerCommitReceiptHandleKind::PublishingStagingRecovery,
                self.receipt_reverify,
                initial_security_readback_sha256,
                &publishing_path,
            )?;
            delete_publishing(&self.root, publishing)?;
            return Ok(());
        }
        if identity.byte_length == 0
            || identity.byte_length > MAX_FINALIZER_COMMIT_RECEIPT_BYTES as u64
        {
            require_same_receipt_capability(
                &publishing,
                FinalizerCommitReceiptHandleKind::PublishingStagingRecovery,
                self.receipt_reverify,
                initial_security_readback_sha256,
                &publishing_path,
            )?;
            delete_publishing(&self.root, publishing)?;
            return Ok(());
        }
        let (bytes, stable_identity, _) = read_bounded_stable(&publishing)?;
        if DurableFinalizerCommitEnvelope::parse_canonical(
            &bytes,
            self.binding,
            self.authenticated_root_sha256,
            stage,
            previous,
        )
        .is_err()
        {
            require_same_receipt_capability(
                &publishing,
                FinalizerCommitReceiptHandleKind::PublishingStagingRecovery,
                self.receipt_reverify,
                initial_security_readback_sha256,
                &publishing_path,
            )?;
            delete_publishing(&self.root, publishing)?;
            return Ok(());
        }
        flush_handle(
            &publishing,
            "authority_finalizer_commit_store_publishing_flush_failed",
        )?;
        require_same_receipt_capability(
            &publishing,
            FinalizerCommitReceiptHandleKind::PublishingStagingRecovery,
            self.receipt_reverify,
            initial_security_readback_sha256,
            &publishing_path,
        )?;
        match rename_relative_no_replace(&publishing, &self.root, final_name)? {
            RenameDisposition::Published => {
                if native_file_identity(&publishing)? != stable_identity {
                    return Err(AuthorityMaintenanceError(
                        "authority_finalizer_commit_store_publishing_identity_drift",
                    ));
                }
                drop(publishing);
                flush_handle(
                    &self.root,
                    "authority_finalizer_commit_store_parent_flush_failed",
                )?;
            }
            RenameDisposition::DestinationExists => {
                require_same_receipt_capability(
                    &publishing,
                    FinalizerCommitReceiptHandleKind::PublishingStagingRecovery,
                    self.receipt_reverify,
                    initial_security_readback_sha256,
                    &publishing_path,
                )?;
                delete_publishing(&self.root, publishing)?;
            }
        }
        Ok(())
    }

    fn recover_authenticated_stage_publication(
        &self,
        stage: FinalizerCommitStage,
        previous: Option<&PersistedReceiptFileReference>,
    ) -> Result<(), AuthorityMaintenanceError> {
        let final_name = stage_file_name(stage);
        let publishing_name = publishing_name(final_name);
        let publishing_path = self.receipt_canonical_path(&publishing_name)?;
        let Some(inspection) = open_relative_optional(
            &self.root,
            &publishing_name,
            AUTHENTICATED_RECEIPT_READ_ACCESS,
        )?
        else {
            return Ok(());
        };
        let identity = native_file_identity(&inspection)?;
        verify_publishable_identity(&identity)?;
        let observed_security = privileged_complete_security_sha256(&inspection)?;
        let staging_security = canonical_security_sha256(STATE_STAGING_SDDL)?;
        let private_sealed_security =
            canonical_security_sha256(FINALIZER_PUBLISHING_PRIVATE_SEALED_SDDL)?;
        let (inspection_kind, sealed) = match classify_publishing_security(
            observed_security,
            staging_security,
            private_sealed_security,
        )? {
            PublishingSecurityPhase::Staging => (
                FinalizerCommitReceiptHandleKind::PublishingInspectionStaging,
                false,
            ),
            PublishingSecurityPhase::PrivateSealed => (
                FinalizerCommitReceiptHandleKind::PublishingInspectionSealed,
                true,
            ),
        };
        let inspection_capability = verify_receipt_capability(
            &inspection,
            inspection_kind,
            self.receipt_reverify,
            &publishing_path,
        )?;
        require_same_receipt_capability(
            &inspection,
            inspection_kind,
            self.receipt_reverify,
            inspection_capability,
            &publishing_path,
        )?;
        drop(inspection);

        if !sealed {
            let staging = open_relative_optional(
                &self.root,
                &publishing_name,
                AUTHENTICATED_RECEIPT_STAGING_RECOVERY_ACCESS,
            )?
            .ok_or(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_publishing_recovery_missing",
            ))?;
            let capability = verify_receipt_capability(
                &staging,
                FinalizerCommitReceiptHandleKind::PublishingStagingRecovery,
                self.receipt_reverify,
                &publishing_path,
            )?;
            if native_file_identity(&staging)? != identity {
                return Err(AuthorityMaintenanceError(
                    "authority_finalizer_commit_store_publishing_identity_drift",
                ));
            }
            if identity.byte_length == 0
                || identity.byte_length > MAX_FINALIZER_COMMIT_RECEIPT_BYTES as u64
            {
                require_same_receipt_capability(
                    &staging,
                    FinalizerCommitReceiptHandleKind::PublishingStagingRecovery,
                    self.receipt_reverify,
                    capability,
                    &publishing_path,
                )?;
                return delete_publishing(&self.root, staging);
            }
            let (bytes, stable_identity, _) = read_bounded_stable(&staging)?;
            if DurableFinalizerCommitEnvelope::parse_canonical(
                &bytes,
                self.binding,
                self.authenticated_root_sha256,
                stage,
                previous,
            )
            .is_err()
            {
                require_same_receipt_capability(
                    &staging,
                    FinalizerCommitReceiptHandleKind::PublishingStagingRecovery,
                    self.receipt_reverify,
                    capability,
                    &publishing_path,
                )?;
                return delete_publishing(&self.root, staging);
            }
            require_same_receipt_capability(
                &staging,
                FinalizerCommitReceiptHandleKind::PublishingStagingRecovery,
                self.receipt_reverify,
                capability,
                &publishing_path,
            )?;
            seal_receipt_security(
                &staging,
                FinalizerCommitReceiptHandleKind::PublishingStagingRecoverySealed,
                self.receipt_reverify,
                &publishing_path,
            )?;
            if native_file_identity(&staging)? != stable_identity {
                return Err(AuthorityMaintenanceError(
                    "authority_finalizer_commit_store_publishing_identity_drift",
                ));
            }
            drop(staging);
            return self.finish_recovered_sealed_publication(
                stage,
                previous,
                &publishing_name,
                stable_identity,
            );
        }

        if identity.byte_length == 0
            || identity.byte_length > MAX_FINALIZER_COMMIT_RECEIPT_BYTES as u64
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_sealed_recovery_invalid",
            ));
        }
        self.finish_recovered_sealed_publication(stage, previous, &publishing_name, identity)
    }

    fn finish_recovered_sealed_publication(
        &self,
        stage: FinalizerCommitStage,
        previous: Option<&PersistedReceiptFileReference>,
        publishing_name: &str,
        expected_identity: NativeFileIdentity,
    ) -> Result<(), AuthorityMaintenanceError> {
        let publishing_path = self.receipt_canonical_path(publishing_name)?;
        let sealed = open_relative_optional(
            &self.root,
            publishing_name,
            AUTHENTICATED_RECEIPT_SEALED_RECOVERY_ACCESS,
        )?
        .ok_or(AuthorityMaintenanceError(
            "authority_finalizer_commit_store_sealed_publishing_missing",
        ))?;
        let capability = verify_receipt_capability(
            &sealed,
            FinalizerCommitReceiptHandleKind::PublishingRecoverySealed,
            self.receipt_reverify,
            &publishing_path,
        )?;
        let (bytes, identity, _) = read_bounded_stable(&sealed)?;
        if identity != expected_identity {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_publishing_identity_drift",
            ));
        }
        DurableFinalizerCommitEnvelope::parse_canonical(
            &bytes,
            self.binding,
            self.authenticated_root_sha256,
            stage,
            previous,
        )
        .map_err(|_| {
            AuthorityMaintenanceError("authority_finalizer_commit_store_sealed_recovery_invalid")
        })?;
        require_same_receipt_capability(
            &sealed,
            FinalizerCommitReceiptHandleKind::PublishingRecoverySealed,
            self.receipt_reverify,
            capability,
            &publishing_path,
        )?;
        finish_sealed_publication(
            &self.root,
            &self.root_canonical_path,
            stage_file_name(stage),
            publishing_name,
            sealed,
            identity,
            &bytes,
            self.receipt_reverify,
        )?;
        Ok(())
    }

    fn verify_root_unchanged(&self) -> Result<(), AuthorityMaintenanceError> {
        let current = native_file_identity(&self.root)?;
        verify_root_identity(&current)?;
        if !same_root_identity(&current, &self.root_identity) {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_root_identity_drift",
            ));
        }
        if let Some(reverify) = self.root_reverify {
            let readback = reverify(&self.root)?;
            if !same_root_identity(&readback.identity, &self.root_identity)
                || authenticated_root_digest(&readback) != self.authenticated_root_sha256
            {
                return Err(AuthorityMaintenanceError(
                    "authority_finalizer_commit_store_root_authentication_drift",
                ));
            }
        }
        let namespace_current = native_file_identity(&self.namespace_root)?;
        verify_root_identity(&namespace_current)?;
        if !same_root_identity(&namespace_current, &self.root_identity) {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_namespace_root_identity_drift",
            ));
        }
        if let (Some(expected), Some(reverify)) =
            (self.namespace_root_readback, self.namespace_root_reverify)
        {
            let readback = reverify(&self.namespace_root)?;
            if !same_authenticated_root_readback(&readback, &expected)
                || !same_root_identity(&readback.identity, &self.root_identity)
                || authenticated_root_digest(&readback) != self.authenticated_root_sha256
            {
                return Err(AuthorityMaintenanceError(
                    "authority_finalizer_commit_namespace_root_authentication_drift",
                ));
            }
        }
        if self.binding.final_commit_store_root_identity_sha256() != self.authenticated_root_sha256
            || self.binding.expected_final_commit_gate_sha256() != self.final_commit_gate_sha256
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_gate_binding_mismatch",
            ));
        }
        Ok(())
    }

    fn final_commit_name_exists(&self) -> Result<bool, AuthorityMaintenanceError> {
        relative_exists(
            &self.root,
            stage_file_name(FinalizerCommitStage::FinalCommit),
            self.receipt_reverify,
        )
    }

    fn receipt_canonical_path(&self, name: &str) -> Result<String, AuthorityMaintenanceError> {
        receipt_canonical_path(&self.root_canonical_path, name)
    }
}

fn typed_transaction_namespace_inventory_from_handle(
    namespace_root: &OwnedHandle,
) -> Result<TypedTransactionNamespaceInventory, AuthorityMaintenanceError> {
    let names = enumerate_transaction_namespace_member_names(namespace_root)
        .map_err(|error| AuthorityMaintenanceError(error.code()))?;
    let mut inventory = TypedTransactionNamespaceInventory::default();
    for name in names {
        if is_typed_finalizer_commit_namespace_name(&name) {
            if name.ends_with(PRIVATE_PUBLISHING_SUFFIX) {
                inventory.private_member_count += 1;
            }
            if !is_protocol_stage_namespace_name(&name) {
                inventory.non_protocol_member_count += 1;
            }
            continue;
        }
        let Some((manifest_sha256, sequence, publishing)) =
            generation_progress_namespace_member(&name)
                .map_err(|error| AuthorityMaintenanceError(error.code()))?
        else {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_transaction_namespace_unknown",
            ));
        };
        if inventory
            .progress_manifest_sha256
            .is_some_and(|observed| observed != manifest_sha256)
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_transaction_progress_manifest_fork",
            ));
        }
        inventory.progress_manifest_sha256 = Some(manifest_sha256);
        let inserted = if publishing {
            inventory.private_member_count += 1;
            inventory.progress_publishing.insert(sequence)
        } else {
            inventory.progress_published.insert(sequence)
        };
        if !inserted {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_transaction_namespace_duplicate",
            ));
        }
    }
    if inventory
        .progress_published
        .iter()
        .enumerate()
        .any(|(index, sequence)| *sequence as usize != index)
        || inventory.progress_publishing.len() > 1
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_transaction_progress_chain_invalid",
        ));
    }
    if let Some(sequence) = inventory.progress_publishing.iter().next().copied() {
        let published_len = inventory.progress_published.len();
        if sequence as usize > published_len
            || (sequence as usize != published_len
                && !inventory.progress_published.contains(&sequence))
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_transaction_progress_chain_invalid",
            ));
        }
    }
    Ok(inventory)
}

fn is_protocol_stage_namespace_name(name: &str) -> bool {
    STAGES.iter().copied().any(|stage| {
        let final_name = stage_file_name(stage);
        name == final_name || name == publishing_name(final_name)
    })
}

fn validate_typed_namespace_for_protocol_state(
    state: Option<&FinalizerCommitProtocolState>,
    inventory: &TypedTransactionNamespaceInventory,
) -> Result<(), AuthorityMaintenanceError> {
    let latest_stage = state.map(FinalizerCommitProtocolState::latest_stage);
    if inventory.progress_manifest_sha256.is_some()
        && latest_stage.map(stage_index).map_or(true, |index| {
            index < stage_index(FinalizerCommitStage::ExitReady)
        })
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_transaction_progress_before_exit_ready",
        ));
    }
    let seal_complete = if let Some(state) = state {
        state.seal_complete_persistence_projection()?
    } else {
        None
    };
    if let Some(seal_complete) = seal_complete {
        let expected_count = usize::try_from(seal_complete.terminal_sequence())
            .ok()
            .and_then(|value| value.checked_add(1))
            .ok_or(AuthorityMaintenanceError(
                "authority_finalizer_commit_transaction_progress_chain_invalid",
            ))?;
        if inventory.progress_manifest_sha256 != Some(seal_complete.manifest_sha256())
            || inventory.progress_published.len() != expected_count
            || !inventory.progress_publishing.is_empty()
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_transaction_progress_binding_mismatch",
            ));
        }
    }
    Ok(())
}

#[derive(Debug)]
struct ScannedStage {
    envelope: DurableFinalizerCommitEnvelope,
    bytes: Vec<u8>,
    native_identity: NativeFileIdentity,
    reference: PersistedReceiptFileReference,
    _held_read_only: OwnedHandle,
}

#[derive(Debug)]
struct StrictPublishedReceiptChain {
    stages: Vec<ScannedStage>,
}

impl StrictPublishedReceiptChain {
    fn tip(&self) -> Result<&ScannedStage, AuthorityMaintenanceError> {
        self.stages.last().ok_or(AuthorityMaintenanceError(
            "authority_finalizer_commit_restricted_chain_incomplete",
        ))
    }

    fn revalidate_held(
        &self,
        transaction: &RestrictedFinalizerCommitTransactionRoot,
    ) -> Result<(), AuthorityMaintenanceError> {
        transaction.verify_unchanged()?;
        let binding = self
            .stages
            .first()
            .ok_or(AuthorityMaintenanceError(
                "authority_finalizer_commit_restricted_chain_incomplete",
            ))?
            .envelope
            .binding;
        let mut previous = None;
        for held in &self.stages {
            let stage = held.envelope.stage;
            let expected_path =
                receipt_canonical_path(&transaction.canonical_path, stage_file_name(stage))?;
            let initial = verify_receipt_capability(
                &held._held_read_only,
                FinalizerCommitReceiptHandleKind::RestrictedPublishedReadOnly,
                transaction.receipt_reverify,
                &expected_path,
            )?;
            let (bytes, identity, bytes_sha256) = read_bounded_stable(&held._held_read_only)?;
            let envelope = DurableFinalizerCommitEnvelope::parse_canonical(
                &bytes,
                binding,
                transaction.authenticated_root_sha256(),
                stage,
                previous.as_ref(),
            )?;
            let final_capability = verify_receipt_capability(
                &held._held_read_only,
                FinalizerCommitReceiptHandleKind::RestrictedPublishedReadOnly,
                transaction.receipt_reverify,
                &expected_path,
            )?;
            let identity_reference = DurableFileIdentity::new(
                identity.volume_serial,
                identity.file_id,
                identity.link_count,
                identity.byte_length,
                bytes_sha256,
            )?;
            let reference = PersistedReceiptFileReference::new(
                stage,
                identity_reference,
                envelope.receipt_sha256,
                envelope.protocol_state_sha256,
                published_receipt_reference_security_sha256(&identity, &expected_path)?,
            )?;
            if initial != final_capability
                || identity != held.native_identity
                || bytes != held.bytes
                || envelope != held.envelope
                || reference != held.reference
            {
                return Err(AuthorityMaintenanceError(
                    "authority_finalizer_commit_restricted_held_receipt_drift",
                ));
            }
            previous = Some(reference);
        }
        transaction.verify_unchanged()
    }
}

fn strict_scan_published_receipt_chain(
    transaction: &RestrictedFinalizerCommitTransactionRoot,
    active_head_transaction_sha256: [u8; 32],
    expected_terminal_stage: FinalizerCommitStage,
) -> Result<StrictPublishedReceiptChain, AuthorityMaintenanceError> {
    if is_zero(&active_head_transaction_sha256)
        || !matches!(
            expected_terminal_stage,
            FinalizerCommitStage::SealComplete | FinalizerCommitStage::FinalCommit
        )
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_restricted_terminal_request_invalid",
        ));
    }
    transaction.verify_unchanged()?;
    let initial_namespace =
        typed_transaction_namespace_inventory_from_handle(&transaction.namespace_root)?;
    if initial_namespace.private_member_count != 0
        || initial_namespace.non_protocol_member_count != 0
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_restricted_namespace_residue",
        ));
    }
    let expected_count = stage_index(expected_terminal_stage) + 1;
    let mut stages = Vec::with_capacity(expected_count);
    let mut binding = None;
    for stage in STAGES.iter().copied().take(expected_count) {
        let previous = stages.last().map(|value: &ScannedStage| &value.reference);
        let scanned = read_strict_published_stage(
            transaction,
            stage,
            active_head_transaction_sha256,
            binding,
            previous,
        )?
        .ok_or(AuthorityMaintenanceError(
            "authority_finalizer_commit_restricted_chain_incomplete",
        ))?;
        if let Some(expected_binding) = binding {
            if scanned.envelope.binding != expected_binding {
                return Err(AuthorityMaintenanceError(
                    "authority_finalizer_commit_restricted_binding_mismatch",
                ));
            }
        } else {
            binding = Some(scanned.envelope.binding);
        }
        stages.push(scanned);
    }
    for stage in STAGES.iter().copied().skip(expected_count) {
        if open_relative_optional(
            &transaction.root,
            stage_file_name(stage),
            transaction.receipt_access,
        )?
        .is_some()
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_restricted_chain_shape_invalid",
            ));
        }
    }
    let tip_state = stages
        .last()
        .ok_or(AuthorityMaintenanceError(
            "authority_finalizer_commit_restricted_chain_incomplete",
        ))?
        .envelope
        .protocol_state
        .as_ref();
    if tip_state.latest_stage() != expected_terminal_stage
        || recovery_directive(expected_terminal_stage)
            != match expected_terminal_stage {
                FinalizerCommitStage::SealComplete => {
                    FinalizerCommitRecoveryDirective::CompleteActiveHeadRuntimeAndPersistFinalCommit
                }
                FinalizerCommitStage::FinalCommit => {
                    FinalizerCommitRecoveryDirective::ReadOnlyVerifyFinalCommitAndRuntime
                }
                _ => unreachable!(),
            }
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_restricted_terminal_state_invalid",
        ));
    }
    validate_typed_namespace_for_protocol_state(Some(tip_state), &initial_namespace)?;
    let final_namespace =
        typed_transaction_namespace_inventory_from_handle(&transaction.namespace_root)?;
    if final_namespace != initial_namespace
        || final_namespace.private_member_count != 0
        || final_namespace.non_protocol_member_count != 0
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_restricted_namespace_drift",
        ));
    }
    transaction.verify_unchanged()?;
    Ok(StrictPublishedReceiptChain { stages })
}

fn read_strict_published_stage(
    transaction: &RestrictedFinalizerCommitTransactionRoot,
    stage: FinalizerCommitStage,
    active_head_transaction_sha256: [u8; 32],
    binding: Option<FinalizerCommitBinding>,
    previous: Option<&PersistedReceiptFileReference>,
) -> Result<Option<ScannedStage>, AuthorityMaintenanceError> {
    let name = stage_file_name(stage);
    let expected_path = receipt_canonical_path(&transaction.canonical_path, name)?;
    let Some(file) = open_relative_optional(&transaction.root, name, transaction.receipt_access)?
    else {
        return Ok(None);
    };
    let initial_capability = verify_receipt_capability(
        &file,
        FinalizerCommitReceiptHandleKind::RestrictedPublishedReadOnly,
        transaction.receipt_reverify,
        &expected_path,
    )?;
    let (bytes, native_identity, bytes_sha256) = read_bounded_stable(&file)?;
    let envelope = if stage == FinalizerCommitStage::TransactionStarted {
        if binding.is_some() || previous.is_some() {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_restricted_genesis_context_invalid",
            ));
        }
        DurableFinalizerCommitEnvelope::parse_transaction_started_self_authenticated(
            &bytes,
            transaction.authenticated_root_sha256(),
            active_head_transaction_sha256,
        )?
    } else {
        DurableFinalizerCommitEnvelope::parse_canonical(
            &bytes,
            binding.ok_or(AuthorityMaintenanceError(
                "authority_finalizer_commit_restricted_genesis_missing",
            ))?,
            transaction.authenticated_root_sha256(),
            stage,
            previous,
        )?
    };
    let final_capability = verify_receipt_capability(
        &file,
        FinalizerCommitReceiptHandleKind::RestrictedPublishedReadOnly,
        transaction.receipt_reverify,
        &expected_path,
    )?;
    if final_capability != initial_capability {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_restricted_receipt_authentication_drift",
        ));
    }
    let identity = DurableFileIdentity::new(
        native_identity.volume_serial,
        native_identity.file_id,
        native_identity.link_count,
        native_identity.byte_length,
        bytes_sha256,
    )?;
    let reference = PersistedReceiptFileReference::new(
        stage,
        identity,
        envelope.receipt_sha256,
        envelope.protocol_state_sha256,
        published_receipt_reference_security_sha256(&native_identity, &expected_path)?,
    )?;
    Ok(Some(ScannedStage {
        envelope,
        bytes,
        native_identity,
        reference,
        _held_read_only: file,
    }))
}

fn recovered_final_commit_readback(
    chain: &StrictPublishedReceiptChain,
    active_head_transaction_sha256: [u8; 32],
    authenticated_root_sha256: [u8; 32],
) -> Result<RecoveredFinalCommitReadback, AuthorityMaintenanceError> {
    if chain.stages.len() != STAGES.len() {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_terminal_readback_incomplete",
        ));
    }
    let tip = chain.tip()?;
    let projection = tip
        .envelope
        .protocol_state
        .final_commit_persistence_projection()?
        .ok_or(AuthorityMaintenanceError(
            "authority_finalizer_final_commit_persistence_projection_missing",
        ))?;
    let binding = projection.binding();
    projection.zero_residue().digest()?;
    if tip.envelope.stage != FinalizerCommitStage::FinalCommit
        || binding != tip.envelope.binding
        || binding.transaction_sha256() != active_head_transaction_sha256
        || binding.final_commit_store_root_identity_sha256() != authenticated_root_sha256
        || projection.expected_final_commit_gate_sha256()
            != binding.expected_final_commit_gate_sha256()
        || projection.final_commit_receipt_sha256() != tip.reference.receipt_sha256()
        || projection.protocol_state_sha256() != tip.reference.protocol_state_sha256()
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_terminal_readback_binding_mismatch",
        ));
    }
    Ok(RecoveredFinalCommitReadback {
        active_head_transaction_sha256,
        authenticated_root_sha256,
        tip: tip.reference,
        projection,
    })
}

fn recovered_seal_complete_readback(
    chain: &StrictPublishedReceiptChain,
    active_head_transaction_sha256: [u8; 32],
    authenticated_root_sha256: [u8; 32],
) -> Result<RecoveredSealCompleteReadback, AuthorityMaintenanceError> {
    if chain.stages.len() != stage_index(FinalizerCommitStage::SealComplete) + 1 {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_precommit_readback_incomplete",
        ));
    }
    let tip = chain.tip()?;
    let binding = tip.envelope.binding;
    let projection = tip
        .envelope
        .protocol_state
        .seal_complete_persistence_projection()?
        .ok_or(AuthorityMaintenanceError(
            "authority_finalizer_seal_complete_persistence_projection_missing",
        ))?;
    if tip.envelope.stage != FinalizerCommitStage::SealComplete
        || binding.transaction_sha256() != active_head_transaction_sha256
        || binding.final_commit_store_root_identity_sha256() != authenticated_root_sha256
        || recovery_directive(tip.envelope.stage)
            != FinalizerCommitRecoveryDirective::CompleteActiveHeadRuntimeAndPersistFinalCommit
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_precommit_readback_binding_mismatch",
        ));
    }
    Ok(RecoveredSealCompleteReadback {
        binding,
        active_head_transaction_sha256,
        authenticated_root_sha256,
        tip: tip.reference,
        projection,
    })
}

fn validate_final_commit_against_active_head(
    readback: &RecoveredFinalCommitReadback,
    active_head: &super::ProtectedActiveHead,
) -> Result<(), AuthorityMaintenanceError> {
    let projection = readback.projection();
    let binding = projection.binding();
    let active_projection = projection.active_head();
    if active_head.transaction_sha256()? != readback.active_head_transaction_sha256()
        || active_head.transaction_sha256()? != binding.transaction_sha256()
        || active_head.plan_sha256()? != binding.plan_sha256()
        || active_head.generation()? != binding.generation_sha256()
        || active_head.digest()? != active_projection.observed_head_sha256()
        || active_head.generation()? != active_projection.committed_generation_sha256()
        || active_head.activation_manifest_sha256()?
            != active_projection.activation_manifest_sha256()
        || active_head.activation_epoch() != active_projection.activation_epoch()
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_active_head_projection_mismatch",
        ));
    }
    Ok(())
}

fn validate_precommit_against_candidate_binding(
    readback: &RecoveredSealCompleteReadback,
    candidate: super::bootstrap_activation::CandidateActivationBinding,
) -> Result<(), AuthorityMaintenanceError> {
    let binding = readback.binding;
    let plan = binding.plan_binding();
    if binding.transaction_sha256() != *candidate.transaction_sha256()
        || binding.capsule_sha256() != *candidate.issuer().capsule_sha256()
        || binding.plan_sha256() != *candidate.plan_sha256()
        || binding.generation_sha256() != *candidate.generation()
        || plan.expected_active_head_prior_sha256() != *candidate.active_head_sha256()
        || plan.expected_activation_manifest_sha256() != *candidate.activation_manifest_sha256()
        || plan.expected_activation_epoch() != candidate.activation_epoch()
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_precommit_credential_binding_mismatch",
        ));
    }
    Ok(())
}

#[derive(Debug, Default, PartialEq, Eq)]
struct TypedTransactionNamespaceInventory {
    progress_manifest_sha256: Option<[u8; 32]>,
    progress_published: BTreeSet<u32>,
    progress_publishing: BTreeSet<u32>,
    private_member_count: usize,
    non_protocol_member_count: usize,
}

#[cfg(test)]
struct ScannedAuxiliary {
    bytes: Vec<u8>,
    reference: PersistedSealAuxiliaryReference,
}

#[cfg(test)]
struct ScannedSealIntent {
    value: DurableFinalizerSealIntent,
    reference: PersistedSealAuxiliaryReference,
}

#[cfg(test)]
struct ScannedSealProgress {
    #[allow(dead_code)]
    value: DurableFinalizerSealProgress,
    reference: PersistedSealAuxiliaryReference,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct NativeFileIdentity {
    volume_serial: u64,
    file_id: [u8; 16],
    byte_length: u64,
    link_count: u32,
    attributes: u32,
}

enum CreateResult {
    Created(NativeFileIdentity),
    Collision,
}

fn publish_relative_atomic(
    parent: &OwnedHandle,
    parent_canonical_path: &str,
    name: &str,
    bytes: &[u8],
    receipt_reverify: Option<ReceiptReverify>,
) -> Result<CreateResult, AuthorityMaintenanceError> {
    if bytes.is_empty() || bytes.len() > MAX_FINALIZER_COMMIT_RECEIPT_BYTES {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_store_receipt_size_invalid",
        ));
    }
    if relative_exists(parent, name, receipt_reverify)? {
        return Ok(CreateResult::Collision);
    }
    let publishing_name = publishing_name(name);
    let explicit_security = if receipt_reverify.is_some() {
        Some(LocalSecurityDescriptor::from_sddl(STATE_STAGING_SDDL)?)
    } else {
        None
    };
    let file = match nt_open_relative(
        parent,
        &publishing_name,
        FILE_CREATE,
        receipt_create_access(receipt_reverify),
        explicit_security
            .as_ref()
            .map(LocalSecurityDescriptor::as_ptr),
    ) {
        Ok(Some(file)) => file,
        Ok(None) => {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_publishing_name_busy",
            ));
        }
        Err(error) => return Err(error),
    };
    let expected_publishing_path = receipt_canonical_path(parent_canonical_path, &publishing_name)?;
    let initial_security_readback_sha256 = verify_receipt_capability(
        &file,
        FinalizerCommitReceiptHandleKind::PublishingCreate,
        receipt_reverify,
        &expected_publishing_path,
    )?;
    write_all(&file, bytes)?;
    flush_handle(
        &file,
        "authority_finalizer_commit_store_receipt_flush_failed",
    )?;
    let identity = native_file_identity(&file)?;
    verify_receipt_identity(&identity, bytes.len() as u64)?;
    let (readback, readback_identity, readback_sha256) = read_bounded_stable(&file)?;
    let expected_sha256: [u8; 32] = Sha256::digest(bytes).into();
    if readback != bytes || readback_identity != identity || readback_sha256 != expected_sha256 {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_store_write_readback_mismatch",
        ));
    }
    let final_security_readback_sha256 = verify_receipt_capability(
        &file,
        FinalizerCommitReceiptHandleKind::PublishingCreate,
        receipt_reverify,
        &expected_publishing_path,
    )?;
    if final_security_readback_sha256 != initial_security_readback_sha256 {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_receipt_authentication_drift",
        ));
    }
    if receipt_reverify.is_some() {
        seal_receipt_security(
            &file,
            FinalizerCommitReceiptHandleKind::PublishingCreateSealed,
            receipt_reverify,
            &expected_publishing_path,
        )?;
        if native_file_identity(&file)? != identity {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_publishing_identity_drift",
            ));
        }
        drop(file);
        let sealed = open_relative_optional(
            parent,
            &publishing_name,
            AUTHENTICATED_RECEIPT_SEALED_RECOVERY_ACCESS,
        )?
        .ok_or(AuthorityMaintenanceError(
            "authority_finalizer_commit_store_sealed_publishing_missing",
        ))?;
        let sealed_capability = verify_receipt_capability(
            &sealed,
            FinalizerCommitReceiptHandleKind::PublishingRecoverySealed,
            receipt_reverify,
            &expected_publishing_path,
        )?;
        let (sealed_bytes, sealed_identity, sealed_sha256) = read_bounded_stable(&sealed)?;
        if sealed_bytes != bytes || sealed_identity != identity || sealed_sha256 != expected_sha256
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_sealed_readback_mismatch",
            ));
        }
        require_same_receipt_capability(
            &sealed,
            FinalizerCommitReceiptHandleKind::PublishingRecoverySealed,
            receipt_reverify,
            sealed_capability,
            &expected_publishing_path,
        )?;
        return finish_sealed_publication(
            parent,
            parent_canonical_path,
            name,
            &publishing_name,
            sealed,
            identity,
            bytes,
            receipt_reverify,
        );
    }
    match rename_relative_no_replace(&file, parent, name)? {
        RenameDisposition::Published => {
            if native_file_identity(&file)? != identity {
                return Err(AuthorityMaintenanceError(
                    "authority_finalizer_commit_store_publishing_identity_drift",
                ));
            }
            drop(file);
            flush_handle(
                parent,
                "authority_finalizer_commit_store_parent_flush_failed",
            )?;
            if relative_exists(parent, &publishing_name, receipt_reverify)? {
                return Err(AuthorityMaintenanceError(
                    "authority_finalizer_commit_store_publishing_residue",
                ));
            }
            Ok(CreateResult::Created(identity))
        }
        RenameDisposition::DestinationExists => {
            require_same_receipt_capability(
                &file,
                FinalizerCommitReceiptHandleKind::PublishingCreate,
                receipt_reverify,
                initial_security_readback_sha256,
                &expected_publishing_path,
            )?;
            delete_publishing(parent, file)?;
            Ok(CreateResult::Collision)
        }
    }
}

fn seal_receipt_security(
    file: &OwnedHandle,
    sealed_kind: FinalizerCommitReceiptHandleKind,
    receipt_reverify: Option<ReceiptReverify>,
    expected_canonical_path: &str,
) -> Result<[u8; 32], AuthorityMaintenanceError> {
    transition_publication_security(
        file,
        FinalizerPublicationSecurityPhase::Staging,
        FinalizerPublicationSecurityPhase::PrivateSealed,
    )
    .map_err(|error| AuthorityMaintenanceError(error.code()))?;
    flush_handle(file, "authority_finalizer_commit_receipt_seal_flush_failed")?;
    verify_receipt_capability(file, sealed_kind, receipt_reverify, expected_canonical_path)
}

#[allow(clippy::too_many_arguments)]
fn finish_sealed_publication(
    parent: &OwnedHandle,
    parent_canonical_path: &str,
    final_name: &str,
    publishing_name: &str,
    sealed: OwnedHandle,
    expected_identity: NativeFileIdentity,
    expected_bytes: &[u8],
    receipt_reverify: Option<ReceiptReverify>,
) -> Result<CreateResult, AuthorityMaintenanceError> {
    let publishing_path = receipt_canonical_path(parent_canonical_path, publishing_name)?;
    match rename_relative_no_replace(&sealed, parent, final_name)? {
        RenameDisposition::Published => {
            if native_file_identity(&sealed)? != expected_identity {
                return Err(AuthorityMaintenanceError(
                    "authority_finalizer_commit_store_publishing_identity_drift",
                ));
            }
            let published_path = receipt_canonical_path(parent_canonical_path, final_name)?;
            verify_receipt_capability(
                &sealed,
                FinalizerCommitReceiptHandleKind::PublishingRecoverySealed,
                receipt_reverify,
                &published_path,
            )?;
            transition_publication_security(
                &sealed,
                FinalizerPublicationSecurityPhase::PrivateSealed,
                FinalizerPublicationSecurityPhase::PublishedImmutable,
            )
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
            flush_handle(
                &sealed,
                "authority_finalizer_commit_receipt_publish_tighten_flush_failed",
            )?;
            verify_receipt_capability(
                &sealed,
                FinalizerCommitReceiptHandleKind::PublishedTightening,
                receipt_reverify,
                &published_path,
            )?;
            if native_file_identity(&sealed)? != expected_identity {
                return Err(AuthorityMaintenanceError(
                    "authority_finalizer_commit_store_publishing_identity_drift",
                ));
            }
            drop(sealed);
            flush_handle(
                parent,
                "authority_finalizer_commit_store_parent_flush_failed",
            )?;
            if relative_exists(parent, publishing_name, receipt_reverify)? {
                return Err(AuthorityMaintenanceError(
                    "authority_finalizer_commit_store_publishing_residue",
                ));
            }
            let published = nt_open_relative(
                parent,
                final_name,
                FILE_OPEN,
                AUTHENTICATED_RECEIPT_READ_ACCESS,
                None,
            )?
            .ok_or(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_published_receipt_missing",
            ))?;
            let initial = verify_receipt_capability(
                &published,
                FinalizerCommitReceiptHandleKind::PublishedReadOnly,
                receipt_reverify,
                &published_path,
            )?;
            let (bytes, identity, bytes_sha256) = read_bounded_stable(&published)?;
            let expected_sha256: [u8; 32] = Sha256::digest(expected_bytes).into();
            if bytes != expected_bytes
                || identity != expected_identity
                || bytes_sha256 != expected_sha256
            {
                return Err(AuthorityMaintenanceError(
                    "authority_finalizer_commit_store_published_readback_mismatch",
                ));
            }
            require_same_receipt_capability(
                &published,
                FinalizerCommitReceiptHandleKind::PublishedReadOnly,
                receipt_reverify,
                initial,
                &published_path,
            )?;
            Ok(CreateResult::Created(expected_identity))
        }
        RenameDisposition::DestinationExists => {
            let published = nt_open_relative(
                parent,
                final_name,
                FILE_OPEN,
                AUTHENTICATED_RECEIPT_READ_ACCESS,
                None,
            )?
            .ok_or(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_collision_receipt_missing",
            ))?;
            let published_path = receipt_canonical_path(parent_canonical_path, final_name)?;
            let published_capability = verify_receipt_capability(
                &published,
                FinalizerCommitReceiptHandleKind::PublishedReadOnly,
                receipt_reverify,
                &published_path,
            )?;
            let (published_bytes, _, published_sha256) = read_bounded_stable(&published)?;
            validate_collision_readback(&published_bytes, published_sha256, expected_bytes)?;
            require_same_receipt_capability(
                &published,
                FinalizerCommitReceiptHandleKind::PublishedReadOnly,
                receipt_reverify,
                published_capability,
                &published_path,
            )?;
            let capability = verify_receipt_capability(
                &sealed,
                FinalizerCommitReceiptHandleKind::PublishingRecoverySealed,
                receipt_reverify,
                &publishing_path,
            )?;
            require_same_receipt_capability(
                &sealed,
                FinalizerCommitReceiptHandleKind::PublishingRecoverySealed,
                receipt_reverify,
                capability,
                &publishing_path,
            )?;
            drop(published);
            delete_publishing(parent, sealed)?;
            Ok(CreateResult::Collision)
        }
    }
}

fn validate_collision_readback(
    published_bytes: &[u8],
    published_sha256: [u8; 32],
    expected_bytes: &[u8],
) -> Result<(), AuthorityMaintenanceError> {
    let expected_sha256: [u8; 32] = Sha256::digest(expected_bytes).into();
    if published_bytes != expected_bytes || published_sha256 != expected_sha256 {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_store_collision_conflict",
        ));
    }
    Ok(())
}

#[cfg(test)]
fn open_root_for_test(path: &Path) -> Result<OwnedHandle, AuthorityMaintenanceError> {
    open_directory_for_test(path, ROOT_ACCESS)
}

#[cfg(test)]
fn open_directory_for_test(
    path: &Path,
    desired_access: u32,
) -> Result<OwnedHandle, AuthorityMaintenanceError> {
    open_absolute_directory_exact(path, desired_access)
}

fn open_absolute_directory_exact(
    path: &Path,
    desired_access: u32,
) -> Result<OwnedHandle, AuthorityMaintenanceError> {
    if !path.is_absolute() || !path_is_local(path) {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_store_root_invalid",
        ));
    }
    let encoded = wide_null(path);
    let handle = unsafe {
        CreateFileW(
            encoded.as_ptr(),
            desired_access,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            ptr::null(),
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
            ptr::null_mut(),
        )
    };
    if handle == INVALID_HANDLE_VALUE {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_store_root_open_failed",
        ));
    }
    let handle = unsafe { OwnedHandle::from_raw_handle(handle as RawHandle) };
    verify_root_identity(&native_file_identity(&handle)?)?;
    Ok(handle)
}

fn access_requires_security_privilege(desired_access: u32) -> bool {
    desired_access & ACCESS_SYSTEM_SECURITY != 0
}

fn with_commit_security_privilege<T>(
    operation: impl FnOnce() -> Result<T, AuthorityMaintenanceError>,
) -> Result<T, AuthorityMaintenanceError> {
    let mut operation_error = None;
    let result = with_finalizer_security_privilege(|| match operation() {
        Ok(value) => Ok(value),
        Err(error) => {
            operation_error = Some(error);
            Err(FinalizerSecurityError::new(
                "authority_finalizer_commit_privileged_open_failed",
            ))
        }
    });
    if result
        .as_ref()
        .is_err_and(|error| error.code() == "authority_finalizer_security_privilege_restore_failed")
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_security_privilege_restore_failed",
        ));
    }
    if let Some(error) = operation_error {
        return Err(error);
    }
    result.map_err(|error| AuthorityMaintenanceError(error.code()))
}

fn privileged_open_preflight(
    handle: &OwnedHandle,
    desired_access: u32,
) -> Result<(), AuthorityMaintenanceError> {
    if !access_requires_security_privilege(desired_access)
        || handle_granted_access(handle)? != desired_access
        || is_zero(
            &complete_security_sha256(handle)
                .map_err(|error| AuthorityMaintenanceError(error.code()))?,
        )
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_privileged_open_preflight_invalid",
        ));
    }
    Ok(())
}

fn nt_open_relative(
    parent: &OwnedHandle,
    name: &str,
    disposition: u32,
    desired_access: u32,
    security_descriptor: Option<PSECURITY_DESCRIPTOR>,
) -> Result<Option<OwnedHandle>, AuthorityMaintenanceError> {
    nt_open_relative_with_share(
        parent,
        name,
        disposition,
        desired_access,
        security_descriptor,
        FILE_SHARE_READ,
    )
}

fn nt_open_relative_with_share(
    parent: &OwnedHandle,
    name: &str,
    disposition: u32,
    desired_access: u32,
    security_descriptor: Option<PSECURITY_DESCRIPTOR>,
    share_access: u32,
) -> Result<Option<OwnedHandle>, AuthorityMaintenanceError> {
    if share_access & !(FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE) != 0 {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_store_share_access_invalid",
        ));
    }
    if access_requires_security_privilege(desired_access) {
        return with_commit_security_privilege(|| {
            let opened = nt_open_relative_unscoped(
                parent,
                name,
                disposition,
                desired_access,
                security_descriptor,
                share_access,
            )?;
            if let Some(handle) = opened.as_ref() {
                privileged_open_preflight(handle, desired_access)?;
            }
            Ok(opened)
        });
    }
    nt_open_relative_unscoped(
        parent,
        name,
        disposition,
        desired_access,
        security_descriptor,
        share_access,
    )
}

fn nt_open_relative_unscoped(
    parent: &OwnedHandle,
    name: &str,
    disposition: u32,
    desired_access: u32,
    security_descriptor: Option<PSECURITY_DESCRIPTOR>,
    share_access: u32,
) -> Result<Option<OwnedHandle>, AuthorityMaintenanceError> {
    validate_relative_name(name)?;
    let mut name_words = name.encode_utf16().collect::<Vec<_>>();
    let name_bytes = name_words
        .len()
        .checked_mul(size_of::<u16>())
        .and_then(|value| u16::try_from(value).ok())
        .ok_or(AuthorityMaintenanceError(
            "authority_finalizer_commit_store_name_invalid",
        ))?;
    let unicode = UNICODE_STRING {
        Length: name_bytes,
        MaximumLength: name_bytes,
        Buffer: name_words.as_mut_ptr(),
    };
    let attributes = OBJECT_ATTRIBUTES {
        Length: size_of::<OBJECT_ATTRIBUTES>() as u32,
        RootDirectory: parent.as_raw_handle().cast(),
        ObjectName: &unicode,
        Attributes: 0,
        SecurityDescriptor: security_descriptor.unwrap_or(ptr::null_mut()),
        SecurityQualityOfService: ptr::null_mut(),
    };
    let mut status_block = unsafe { zeroed::<IO_STATUS_BLOCK>() };
    let mut handle = ptr::null_mut();
    let status = unsafe {
        NtCreateFile(
            &mut handle,
            desired_access,
            &attributes,
            &mut status_block,
            ptr::null(),
            FILE_ATTRIBUTE_NORMAL,
            share_access,
            disposition,
            FILE_SYNCHRONOUS_IO_NONALERT
                | FILE_NON_DIRECTORY_FILE
                | FILE_OPEN_REPARSE_POINT
                | FILE_WRITE_THROUGH,
            ptr::null(),
            0,
        )
    };
    if disposition == FILE_CREATE && status == STATUS_OBJECT_NAME_COLLISION {
        return Ok(None);
    }
    if disposition == FILE_OPEN
        && matches!(
            status,
            STATUS_NO_SUCH_FILE | STATUS_OBJECT_NAME_NOT_FOUND | STATUS_OBJECT_PATH_NOT_FOUND
        )
    {
        return Ok(None);
    }
    if status < 0 || handle.is_null() || handle == INVALID_HANDLE_VALUE {
        return Err(AuthorityMaintenanceError(if disposition == FILE_CREATE {
            "authority_finalizer_commit_store_receipt_create_failed"
        } else {
            "authority_finalizer_commit_store_receipt_open_failed"
        }));
    }
    let expected_information = if disposition == FILE_CREATE {
        FILE_CREATED_INFORMATION
    } else {
        FILE_OPENED_INFORMATION
    };
    if status_block.Information != expected_information {
        unsafe { drop(OwnedHandle::from_raw_handle(handle as RawHandle)) };
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_store_open_result_invalid",
        ));
    }
    Ok(Some(unsafe {
        OwnedHandle::from_raw_handle(handle as RawHandle)
    }))
}

fn nt_open_relative_directory(
    parent: &OwnedHandle,
    name: &str,
    desired_access: u32,
) -> Result<Option<OwnedHandle>, AuthorityMaintenanceError> {
    nt_open_relative_directory_with_share(
        parent,
        name,
        desired_access,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
    )
}

fn nt_open_relative_directory_with_share(
    parent: &OwnedHandle,
    name: &str,
    desired_access: u32,
    share_access: u32,
) -> Result<Option<OwnedHandle>, AuthorityMaintenanceError> {
    if access_requires_security_privilege(desired_access) {
        return with_commit_security_privilege(|| {
            let opened =
                nt_open_relative_directory_unscoped(parent, name, desired_access, share_access)?;
            if let Some(handle) = opened.as_ref() {
                privileged_open_preflight(handle, desired_access)?;
            }
            Ok(opened)
        });
    }
    nt_open_relative_directory_unscoped(parent, name, desired_access, share_access)
}

fn nt_open_relative_directory_unscoped(
    parent: &OwnedHandle,
    name: &str,
    desired_access: u32,
    share_access: u32,
) -> Result<Option<OwnedHandle>, AuthorityMaintenanceError> {
    validate_relative_name(name)?;
    let mut name_words = name.encode_utf16().collect::<Vec<_>>();
    let name_bytes = name_words
        .len()
        .checked_mul(size_of::<u16>())
        .and_then(|value| u16::try_from(value).ok())
        .ok_or(AuthorityMaintenanceError(
            "authority_finalizer_commit_store_name_invalid",
        ))?;
    let unicode = UNICODE_STRING {
        Length: name_bytes,
        MaximumLength: name_bytes,
        Buffer: name_words.as_mut_ptr(),
    };
    let attributes = OBJECT_ATTRIBUTES {
        Length: size_of::<OBJECT_ATTRIBUTES>() as u32,
        RootDirectory: parent.as_raw_handle().cast(),
        ObjectName: &unicode,
        Attributes: 0,
        SecurityDescriptor: ptr::null_mut(),
        SecurityQualityOfService: ptr::null_mut(),
    };
    let mut status_block = unsafe { zeroed::<IO_STATUS_BLOCK>() };
    let mut handle = ptr::null_mut();
    let status = unsafe {
        NtCreateFile(
            &mut handle,
            desired_access,
            &attributes,
            &mut status_block,
            ptr::null(),
            FILE_ATTRIBUTE_NORMAL,
            share_access,
            FILE_OPEN,
            FILE_SYNCHRONOUS_IO_NONALERT | FILE_DIRECTORY_FILE | FILE_OPEN_REPARSE_POINT,
            ptr::null(),
            0,
        )
    };
    if matches!(
        status,
        STATUS_NO_SUCH_FILE | STATUS_OBJECT_NAME_NOT_FOUND | STATUS_OBJECT_PATH_NOT_FOUND
    ) {
        return Ok(None);
    }
    if status < 0
        || handle.is_null()
        || handle == INVALID_HANDLE_VALUE
        || status_block.Information != FILE_OPENED_INFORMATION
    {
        if !handle.is_null() && handle != INVALID_HANDLE_VALUE {
            unsafe { drop(OwnedHandle::from_raw_handle(handle as RawHandle)) };
        }
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_transaction_root_open_failed",
        ));
    }
    Ok(Some(unsafe {
        OwnedHandle::from_raw_handle(handle as RawHandle)
    }))
}

fn open_relative_readonly_optional(
    parent: &OwnedHandle,
    name: &str,
    receipt_reverify: Option<ReceiptReverify>,
) -> Result<Option<OwnedHandle>, AuthorityMaintenanceError> {
    nt_open_relative(
        parent,
        name,
        FILE_OPEN,
        receipt_read_access(receipt_reverify),
        None,
    )
}

fn open_relative_optional(
    parent: &OwnedHandle,
    name: &str,
    desired_access: u32,
) -> Result<Option<OwnedHandle>, AuthorityMaintenanceError> {
    nt_open_relative(parent, name, FILE_OPEN, desired_access, None)
}

fn relative_exists(
    parent: &OwnedHandle,
    name: &str,
    receipt_reverify: Option<ReceiptReverify>,
) -> Result<bool, AuthorityMaintenanceError> {
    Ok(open_relative_readonly_optional(parent, name, receipt_reverify)?.is_some())
}

fn receipt_read_access(receipt_reverify: Option<ReceiptReverify>) -> u32 {
    if receipt_reverify.is_some() {
        AUTHENTICATED_RECEIPT_READ_ACCESS
    } else {
        RECEIPT_READ_ACCESS
    }
}

fn receipt_create_access(receipt_reverify: Option<ReceiptReverify>) -> u32 {
    if receipt_reverify.is_some() {
        AUTHENTICATED_RECEIPT_CREATE_ACCESS
    } else {
        RECEIPT_CREATE_ACCESS
    }
}

fn publishing_name(final_name: &str) -> String {
    format!("{final_name}{PRIVATE_PUBLISHING_SUFFIX}")
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum RenameDisposition {
    Published,
    DestinationExists,
}

fn rename_relative_no_replace(
    file: &OwnedHandle,
    parent: &OwnedHandle,
    destination_name: &str,
) -> Result<RenameDisposition, AuthorityMaintenanceError> {
    validate_relative_name(destination_name)?;
    let name = destination_name.encode_utf16().collect::<Vec<_>>();
    let name_byte_length = name
        .len()
        .checked_mul(size_of::<u16>())
        .and_then(|value| u32::try_from(value).ok())
        .ok_or(AuthorityMaintenanceError(
            "authority_finalizer_commit_store_name_invalid",
        ))?;
    let file_name_offset = std::mem::offset_of!(FILE_RENAME_INFORMATION, FileName);
    let total_length = file_name_offset
        .checked_add(name_byte_length as usize)
        .ok_or(AuthorityMaintenanceError(
            "authority_finalizer_commit_store_name_invalid",
        ))?;
    let storage_words = total_length
        .checked_add(size_of::<usize>() - 1)
        .map(|value| value / size_of::<usize>())
        .ok_or(AuthorityMaintenanceError(
            "authority_finalizer_commit_store_name_invalid",
        ))?;
    let mut storage = vec![0usize; storage_words];
    let information = storage.as_mut_ptr().cast::<FILE_RENAME_INFORMATION>();
    unsafe {
        (*information).Anonymous.ReplaceIfExists = 0;
        (*information).RootDirectory = parent.as_raw_handle().cast();
        (*information).FileNameLength = name_byte_length;
        ptr::copy_nonoverlapping(
            name.as_ptr(),
            storage
                .as_mut_ptr()
                .cast::<u8>()
                .add(file_name_offset)
                .cast(),
            name.len(),
        );
    }
    let mut status = unsafe { zeroed::<IO_STATUS_BLOCK>() };
    let status_code = unsafe {
        NtSetInformationFile(
            file.as_raw_handle().cast(),
            &mut status,
            information.cast(),
            total_length as u32,
            FileRenameInformation,
        )
    };
    if status_code == STATUS_OBJECT_NAME_COLLISION {
        return Ok(RenameDisposition::DestinationExists);
    }
    if status_code < 0 {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_store_publish_failed",
        ));
    }
    Ok(RenameDisposition::Published)
}

fn delete_publishing(
    parent: &OwnedHandle,
    publishing: OwnedHandle,
) -> Result<(), AuthorityMaintenanceError> {
    let identity = native_file_identity(&publishing)?;
    verify_publishable_identity(&identity)?;
    let disposition = FILE_DISPOSITION_INFORMATION_EX {
        Flags: FILE_DISPOSITION_DELETE,
    };
    let mut status = unsafe { zeroed::<IO_STATUS_BLOCK>() };
    if unsafe {
        NtSetInformationFile(
            publishing.as_raw_handle().cast(),
            &mut status,
            (&disposition as *const FILE_DISPOSITION_INFORMATION_EX).cast(),
            size_of::<FILE_DISPOSITION_INFORMATION_EX>() as u32,
            FileDispositionInformationEx,
        )
    } < 0
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_store_publishing_delete_failed",
        ));
    }
    drop(publishing);
    flush_handle(
        parent,
        "authority_finalizer_commit_store_parent_flush_failed",
    )?;
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

struct LocalSecurityDescriptor(PSECURITY_DESCRIPTOR);

impl LocalSecurityDescriptor {
    fn from_sddl(value: &str) -> Result<Self, AuthorityMaintenanceError> {
        if value.is_empty() || value.len() > 16 * 1024 || value.contains('\0') {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_security_descriptor_invalid",
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
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_security_descriptor_invalid",
            ));
        }
        Ok(Self(descriptor))
    }

    fn as_ptr(&self) -> PSECURITY_DESCRIPTOR {
        self.0
    }

    fn acl_parts(&self) -> Result<(*mut ACL, *mut ACL), AuthorityMaintenanceError> {
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
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_receipt_seal_dacl_invalid",
            ));
        }
        let mut label_present = 0;
        let mut label_defaulted = 0;
        let mut label = ptr::null_mut();
        if unsafe {
            GetSecurityDescriptorSacl(self.0, &mut label_present, &mut label, &mut label_defaulted)
        } == 0
            || label_present == 0
            || label_defaulted != 0
            || label.is_null()
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_receipt_seal_label_invalid",
            ));
        }
        Ok((dacl, label))
    }
}

impl Drop for LocalSecurityDescriptor {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe {
                LocalFree(self.0.cast());
            }
        }
    }
}

fn authenticate_transaction_root_reverify(
    handle: &OwnedHandle,
) -> Result<AuthenticatedFinalizerCommitRootReadback, AuthorityMaintenanceError> {
    authenticate_commit_root_handle(handle, FinalizerCommitRootHandleKind::TransactionRoot)
}

fn authenticate_transaction_namespace_root_reverify(
    handle: &OwnedHandle,
) -> Result<AuthenticatedFinalizerCommitRootReadback, AuthorityMaintenanceError> {
    authenticate_commit_root_handle(
        handle,
        FinalizerCommitRootHandleKind::TransactionNamespaceRoot,
    )
}

fn authenticate_restricted_transaction_root_reverify(
    handle: &OwnedHandle,
) -> Result<AuthenticatedFinalizerCommitRootReadback, AuthorityMaintenanceError> {
    authenticate_restricted_commit_root_handle(
        handle,
        RestrictedFinalizerCommitRootHandleKind::TransactionRoot,
    )
}

fn authenticate_restricted_commit_root_handle(
    handle: &OwnedHandle,
    kind: RestrictedFinalizerCommitRootHandleKind,
) -> Result<AuthenticatedFinalizerCommitRootReadback, AuthorityMaintenanceError> {
    let identity = native_file_identity(handle)?;
    verify_root_identity(&identity)?;
    require_case_sensitivity_disabled(handle)?;
    let granted_access = handle_granted_access(handle)?;
    let complete_security_readback_sha256 = restricted_complete_security_sha256(handle)?;
    validate_restricted_root_capability_observation(
        kind,
        granted_access,
        complete_security_readback_sha256,
    )?;
    let canonical_path = canonical_handle_path(handle)?;
    Ok(AuthenticatedFinalizerCommitRootReadback {
        identity,
        canonical_path_readback_sha256: canonical_path_sha256(&canonical_path),
        complete_security_readback_sha256,
        granted_access_readback_sha256: restricted_granted_access_sha256(kind, granted_access),
    })
}

fn validate_restricted_root_capability_observation(
    kind: RestrictedFinalizerCommitRootHandleKind,
    granted_access: u32,
    complete_security_readback_sha256: [u8; 32],
) -> Result<(), AuthorityMaintenanceError> {
    let (expected_access, expected_security) = match kind {
        RestrictedFinalizerCommitRootHandleKind::StateRoot
        | RestrictedFinalizerCommitRootHandleKind::ParentNamespace => {
            (RESTRICTED_PARENT_ROOT_ACCESS, STABLE_ROOT_SDDL)
        }
        RestrictedFinalizerCommitRootHandleKind::TransactionRoot => (
            RESTRICTED_TRANSACTION_ROOT_ACCESS,
            FINALIZER_COMMIT_TRANSACTION_ROOT_SDDL,
        ),
        RestrictedFinalizerCommitRootHandleKind::GenerationNamespace => {
            (RESTRICTED_RUNTIME_GENERATION_ROOT_ACCESS, STABLE_ROOT_SDDL)
        }
        RestrictedFinalizerCommitRootHandleKind::SealedGeneration => (
            RESTRICTED_RUNTIME_GENERATION_ROOT_ACCESS,
            GENERATION_SEALED_SDDL,
        ),
        RestrictedFinalizerCommitRootHandleKind::RuntimeBlobDirectory => (
            RESTRICTED_RUNTIME_BLOB_DIRECTORY_ACCESS,
            RUNTIME_BLOB_DIRECTORY_FINAL_SDDL,
        ),
    };
    if granted_access != expected_access
        || granted_access & (ACCESS_SYSTEM_SECURITY | DELETE | WRITE_DAC) != 0
        || granted_access & 0xf000_0000 != 0
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_restricted_root_access_invalid",
        ));
    }
    if complete_security_readback_sha256 != canonical_security_sha256(expected_security)? {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_restricted_root_security_invalid",
        ));
    }
    Ok(())
}

fn restricted_granted_access_sha256(
    kind: RestrictedFinalizerCommitRootHandleKind,
    granted_access: u32,
) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(b"vrcforge-authority-finalizer-commit-restricted-root-access-v1\0");
    digest.update([match kind {
        RestrictedFinalizerCommitRootHandleKind::StateRoot => 1,
        RestrictedFinalizerCommitRootHandleKind::ParentNamespace => 2,
        RestrictedFinalizerCommitRootHandleKind::TransactionRoot => 3,
        RestrictedFinalizerCommitRootHandleKind::GenerationNamespace => 4,
        RestrictedFinalizerCommitRootHandleKind::SealedGeneration => 5,
        RestrictedFinalizerCommitRootHandleKind::RuntimeBlobDirectory => 6,
    }]);
    digest.update(granted_access.to_be_bytes());
    digest.finalize().into()
}

fn validate_runtime_generation_root_provenance(
    child: &AuthenticatedFinalizerCommitRootReadback,
    parent: &AuthenticatedFinalizerCommitRootReadback,
    expected_canonical_path: &str,
) -> Result<(), AuthorityMaintenanceError> {
    if child.identity.volume_serial != parent.identity.volume_serial
        || child.canonical_path_readback_sha256 != canonical_path_sha256(expected_canonical_path)
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_runtime_generation_provenance_mismatch",
        ));
    }
    Ok(())
}

fn authenticate_restricted_runtime_ledger_handle(
    handle: &impl AsRawHandle,
    expected_canonical_path: &str,
    expected_volume_serial: u64,
) -> Result<RestrictedRuntimeLedgerFileReadback, AuthorityMaintenanceError> {
    let native = native_file_identity(handle)?;
    if native.volume_serial != expected_volume_serial
        || native.link_count != 1
        || native.byte_length == 0
        || native.byte_length > MAX_RESTRICTED_RUNTIME_LEDGER_BYTES
        || native.attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT) != 0
        || native.file_id.iter().all(|byte| *byte == 0)
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_runtime_ledger_identity_invalid",
        ));
    }
    let canonical_path = canonical_handle_path(handle)?;
    if canonical_path != expected_canonical_path {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_runtime_ledger_path_mismatch",
        ));
    }
    let granted_access = handle_granted_access(handle)?;
    let complete_security_readback_sha256 = restricted_complete_security_sha256(handle)?;
    validate_restricted_runtime_ledger_capability_observation(
        granted_access,
        complete_security_readback_sha256,
    )?;
    let identity = RestrictedRuntimeLedgerFileIdentity {
        volume_serial: native.volume_serial,
        file_id: native.file_id,
        link_count: native.link_count,
        attributes: native.attributes,
    };
    Ok(RestrictedRuntimeLedgerFileReadback {
        identity,
        canonical_path_readback_sha256: canonical_path_sha256(&canonical_path),
        complete_security_readback_sha256,
        granted_access_readback_sha256: restricted_runtime_ledger_access_sha256(granted_access),
    })
}

fn validate_restricted_runtime_ledger_capability_observation(
    granted_access: u32,
    complete_security_readback_sha256: [u8; 32],
) -> Result<(), AuthorityMaintenanceError> {
    if granted_access != RESTRICTED_RUNTIME_LEDGER_ACCESS
        || granted_access
            & (ACCESS_SYSTEM_SECURITY | DELETE | WRITE_DAC | WRITE_OWNER_ACCESS | 0xf000_0000)
            != 0
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_runtime_ledger_access_invalid",
        ));
    }
    if complete_security_readback_sha256 != canonical_security_sha256(LEDGER_FINAL_SDDL)? {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_runtime_ledger_security_invalid",
        ));
    }
    Ok(())
}

fn restricted_runtime_ledger_access_sha256(granted_access: u32) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(b"vrcforge-authority-finalizer-runtime-ledger-access-v1\0");
    digest.update(granted_access.to_be_bytes());
    digest.finalize().into()
}

fn same_runtime_ledger_file_identity(
    left: &RestrictedRuntimeLedgerFileIdentity,
    right: &RestrictedRuntimeLedgerFileIdentity,
) -> bool {
    left.volume_serial == right.volume_serial
        && left.file_id == right.file_id
        && left.link_count == right.link_count
        && left.attributes == right.attributes
}

fn require_case_sensitivity_disabled(
    handle: &OwnedHandle,
) -> Result<(), AuthorityMaintenanceError> {
    let mut information = FileCaseSensitiveInformation { flags: 0 };
    if unsafe {
        GetFileInformationByHandleEx(
            handle.as_raw_handle().cast(),
            FileCaseSensitiveInfo,
            (&mut information as *mut FileCaseSensitiveInformation).cast(),
            size_of::<FileCaseSensitiveInformation>() as u32,
        )
    } == 0
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_root_case_sensitivity_unavailable",
        ));
    }
    validate_case_sensitivity_flags(information.flags)
}

fn validate_case_sensitivity_flags(flags: u32) -> Result<(), AuthorityMaintenanceError> {
    if flags & FILE_CS_FLAG_CASE_SENSITIVE_DIR != 0 {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_root_case_sensitive",
        ));
    }
    if flags != 0 {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_root_case_sensitivity_flags_invalid",
        ));
    }
    Ok(())
}

fn authenticate_commit_root_handle(
    handle: &OwnedHandle,
    kind: FinalizerCommitRootHandleKind,
) -> Result<AuthenticatedFinalizerCommitRootReadback, AuthorityMaintenanceError> {
    let identity = native_file_identity(handle)?;
    verify_root_identity(&identity)?;
    require_case_sensitivity_disabled(handle)?;
    let expected_access = match kind {
        FinalizerCommitRootHandleKind::ParentNamespace => AUTHENTICATED_PARENT_ROOT_ACCESS,
        FinalizerCommitRootHandleKind::TransactionRoot => AUTHENTICATED_TRANSACTION_ROOT_ACCESS,
        FinalizerCommitRootHandleKind::TransactionProgressRoot => {
            AUTHENTICATED_TRANSACTION_PROGRESS_ROOT_ACCESS
        }
        FinalizerCommitRootHandleKind::TransactionNamespaceRoot => {
            AUTHENTICATED_TRANSACTION_NAMESPACE_ROOT_ACCESS
        }
    };
    let granted_access = handle_granted_access(handle)?;
    if granted_access != expected_access || granted_access & 0xf000_0000 != 0 {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_root_access_invalid",
        ));
    }
    let complete_security_readback_sha256 = privileged_complete_security_sha256(handle)?;
    let expected_security = match kind {
        FinalizerCommitRootHandleKind::ParentNamespace => STABLE_ROOT_SDDL,
        FinalizerCommitRootHandleKind::TransactionRoot
        | FinalizerCommitRootHandleKind::TransactionProgressRoot
        | FinalizerCommitRootHandleKind::TransactionNamespaceRoot => {
            FINALIZER_COMMIT_TRANSACTION_ROOT_SDDL
        }
    };
    if complete_security_readback_sha256 != canonical_security_sha256(expected_security)? {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_root_security_invalid",
        ));
    }
    let canonical_path = canonical_handle_path(handle)?;
    Ok(AuthenticatedFinalizerCommitRootReadback {
        identity,
        canonical_path_readback_sha256: canonical_path_sha256(&canonical_path),
        complete_security_readback_sha256,
        granted_access_readback_sha256: granted_access_sha256(kind, granted_access),
    })
}

fn authenticate_receipt_handle(
    handle: &OwnedHandle,
    kind: FinalizerCommitReceiptHandleKind,
    expected_canonical_path: &str,
) -> Result<[u8; 32], AuthorityMaintenanceError> {
    let identity = native_file_identity(handle)?;
    verify_publishable_identity(&identity)?;
    let (expected_access, expected_security) = receipt_capability_expectation(kind);
    let granted_access = handle_granted_access(handle)?;
    let complete_security_readback_sha256 = privileged_complete_security_sha256(handle)?;
    validate_receipt_capability_observation(
        kind,
        granted_access,
        complete_security_readback_sha256,
    )?;
    let canonical_path = canonical_handle_path(handle)?;
    if canonical_path != expected_canonical_path {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_receipt_path_mismatch",
        ));
    }
    let mut digest = Sha256::new();
    digest.update(b"vrcforge-authority-finalizer-commit-receipt-capability-v1\0");
    digest.update([receipt_handle_kind_tag(kind)]);
    digest.update(identity.volume_serial.to_be_bytes());
    digest.update(identity.file_id);
    digest.update(identity.link_count.to_be_bytes());
    digest.update(canonical_path_sha256(&canonical_path));
    digest.update(complete_security_readback_sha256);
    digest.update(granted_access.to_be_bytes());
    debug_assert_eq!(
        complete_security_readback_sha256,
        canonical_security_sha256(expected_security)?
    );
    debug_assert_eq!(granted_access, expected_access);
    Ok(digest.finalize().into())
}

fn authenticate_restricted_receipt_handle(
    handle: &OwnedHandle,
    kind: FinalizerCommitReceiptHandleKind,
    expected_canonical_path: &str,
) -> Result<[u8; 32], AuthorityMaintenanceError> {
    if kind != FinalizerCommitReceiptHandleKind::RestrictedPublishedReadOnly {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_restricted_receipt_kind_invalid",
        ));
    }
    let identity = native_file_identity(handle)?;
    verify_publishable_identity(&identity)?;
    let granted_access = handle_granted_access(handle)?;
    let complete_security_readback_sha256 = restricted_complete_security_sha256(handle)?;
    validate_restricted_receipt_capability_observation(
        granted_access,
        complete_security_readback_sha256,
    )?;
    let canonical_path = canonical_handle_path(handle)?;
    if canonical_path != expected_canonical_path {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_receipt_path_mismatch",
        ));
    }
    let mut digest = Sha256::new();
    digest.update(b"vrcforge-authority-finalizer-commit-restricted-receipt-access-v1\0");
    digest.update(identity.volume_serial.to_be_bytes());
    digest.update(identity.file_id);
    digest.update(identity.link_count.to_be_bytes());
    digest.update(canonical_path_sha256(&canonical_path));
    digest.update(complete_security_readback_sha256);
    digest.update(granted_access.to_be_bytes());
    Ok(digest.finalize().into())
}

fn authenticate_restricted_active_head_handle(
    handle: &OwnedHandle,
    expected_canonical_path: &str,
) -> Result<[u8; 32], AuthorityMaintenanceError> {
    let identity = native_file_identity(handle)?;
    verify_publishable_identity(&identity)?;
    let granted_access = handle_granted_access(handle)?;
    let complete_security_readback_sha256 = restricted_complete_security_sha256(handle)?;
    if granted_access != RESTRICTED_RECEIPT_READ_ACCESS
        || granted_access & (ACCESS_SYSTEM_SECURITY | DELETE | WRITE_DAC | FILE_WRITE_DATA) != 0
        || complete_security_readback_sha256 != canonical_security_sha256(STATE_IMMUTABLE_SDDL)?
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_active_head_capability_invalid",
        ));
    }
    let canonical_path = canonical_handle_path(handle)?;
    if canonical_path != expected_canonical_path {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_active_head_path_mismatch",
        ));
    }
    let mut digest = Sha256::new();
    digest.update(b"vrcforge-authority-finalizer-commit-active-head-capability-v1\0");
    digest.update(identity.volume_serial.to_be_bytes());
    digest.update(identity.file_id);
    digest.update(identity.link_count.to_be_bytes());
    digest.update(canonical_path_sha256(&canonical_path));
    digest.update(complete_security_readback_sha256);
    digest.update(granted_access.to_be_bytes());
    Ok(digest.finalize().into())
}

fn validate_restricted_receipt_capability_observation(
    granted_access: u32,
    complete_security_readback_sha256: [u8; 32],
) -> Result<(), AuthorityMaintenanceError> {
    if granted_access != RESTRICTED_RECEIPT_READ_ACCESS
        || granted_access & (ACCESS_SYSTEM_SECURITY | DELETE | WRITE_DAC | FILE_WRITE_DATA) != 0
        || granted_access & 0xf000_0000 != 0
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_restricted_receipt_access_invalid",
        ));
    }
    if complete_security_readback_sha256
        != canonical_security_sha256(FINALIZER_COMMIT_RECEIPT_IMMUTABLE_SDDL)?
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_restricted_receipt_security_invalid",
        ));
    }
    Ok(())
}

fn receipt_capability_expectation(kind: FinalizerCommitReceiptHandleKind) -> (u32, &'static str) {
    match kind {
        FinalizerCommitReceiptHandleKind::PublishingInspectionStaging => {
            (AUTHENTICATED_RECEIPT_READ_ACCESS, STATE_STAGING_SDDL)
        }
        FinalizerCommitReceiptHandleKind::PublishingInspectionSealed => (
            AUTHENTICATED_RECEIPT_READ_ACCESS,
            FINALIZER_PUBLISHING_PRIVATE_SEALED_SDDL,
        ),
        FinalizerCommitReceiptHandleKind::PublishingCreate => {
            (AUTHENTICATED_RECEIPT_CREATE_ACCESS, STATE_STAGING_SDDL)
        }
        FinalizerCommitReceiptHandleKind::PublishingStagingRecovery => (
            AUTHENTICATED_RECEIPT_STAGING_RECOVERY_ACCESS,
            STATE_STAGING_SDDL,
        ),
        FinalizerCommitReceiptHandleKind::PublishingCreateSealed => (
            AUTHENTICATED_RECEIPT_CREATE_ACCESS,
            FINALIZER_PUBLISHING_PRIVATE_SEALED_SDDL,
        ),
        FinalizerCommitReceiptHandleKind::PublishingStagingRecoverySealed => (
            AUTHENTICATED_RECEIPT_STAGING_RECOVERY_ACCESS,
            FINALIZER_PUBLISHING_PRIVATE_SEALED_SDDL,
        ),
        FinalizerCommitReceiptHandleKind::PublishingRecoverySealed => (
            AUTHENTICATED_RECEIPT_SEALED_RECOVERY_ACCESS,
            FINALIZER_PUBLISHING_PRIVATE_SEALED_SDDL,
        ),
        FinalizerCommitReceiptHandleKind::PublishedTightening => (
            AUTHENTICATED_RECEIPT_SEALED_RECOVERY_ACCESS,
            FINALIZER_COMMIT_RECEIPT_IMMUTABLE_SDDL,
        ),
        FinalizerCommitReceiptHandleKind::PublishedReadOnly => (
            AUTHENTICATED_RECEIPT_READ_ACCESS,
            FINALIZER_COMMIT_RECEIPT_IMMUTABLE_SDDL,
        ),
        FinalizerCommitReceiptHandleKind::RestrictedPublishedReadOnly => (
            RESTRICTED_RECEIPT_READ_ACCESS,
            FINALIZER_COMMIT_RECEIPT_IMMUTABLE_SDDL,
        ),
    }
}

fn validate_receipt_capability_observation(
    kind: FinalizerCommitReceiptHandleKind,
    granted_access: u32,
    complete_security_readback_sha256: [u8; 32],
) -> Result<(), AuthorityMaintenanceError> {
    let (expected_access, expected_security) = receipt_capability_expectation(kind);
    if granted_access != expected_access || granted_access & 0xf000_0000 != 0 {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_receipt_access_invalid",
        ));
    }
    if complete_security_readback_sha256 != canonical_security_sha256(expected_security)? {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_receipt_security_invalid",
        ));
    }
    Ok(())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum PublishingSecurityPhase {
    Staging,
    PrivateSealed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum FinalReceiptSecurityPhase {
    PrivateSealed,
    PublishedImmutable,
}

fn classify_publishing_security(
    observed: [u8; 32],
    staging: [u8; 32],
    sealed: [u8; 32],
) -> Result<PublishingSecurityPhase, AuthorityMaintenanceError> {
    if observed == staging && staging != sealed {
        Ok(PublishingSecurityPhase::Staging)
    } else if observed == sealed && staging != sealed {
        Ok(PublishingSecurityPhase::PrivateSealed)
    } else {
        Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_receipt_recovery_security_invalid",
        ))
    }
}

fn classify_final_receipt_security(
    observed: [u8; 32],
) -> Result<FinalReceiptSecurityPhase, AuthorityMaintenanceError> {
    let private_sealed = canonical_security_sha256(FINALIZER_PUBLISHING_PRIVATE_SEALED_SDDL)?;
    let published_immutable = canonical_security_sha256(FINALIZER_COMMIT_RECEIPT_IMMUTABLE_SDDL)?;
    if observed == private_sealed && private_sealed != published_immutable {
        Ok(FinalReceiptSecurityPhase::PrivateSealed)
    } else if observed == published_immutable && private_sealed != published_immutable {
        Ok(FinalReceiptSecurityPhase::PublishedImmutable)
    } else {
        Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_final_receipt_security_invalid",
        ))
    }
}

fn receipt_handle_kind_tag(kind: FinalizerCommitReceiptHandleKind) -> u8 {
    match kind {
        FinalizerCommitReceiptHandleKind::PublishingInspectionStaging => 1,
        FinalizerCommitReceiptHandleKind::PublishingInspectionSealed => 2,
        FinalizerCommitReceiptHandleKind::PublishingCreate => 3,
        FinalizerCommitReceiptHandleKind::PublishingStagingRecovery => 4,
        FinalizerCommitReceiptHandleKind::PublishingCreateSealed => 5,
        FinalizerCommitReceiptHandleKind::PublishingStagingRecoverySealed => 6,
        FinalizerCommitReceiptHandleKind::PublishingRecoverySealed => 7,
        FinalizerCommitReceiptHandleKind::PublishedTightening => 8,
        FinalizerCommitReceiptHandleKind::PublishedReadOnly => 9,
        FinalizerCommitReceiptHandleKind::RestrictedPublishedReadOnly => 10,
    }
}

fn granted_access_sha256(kind: FinalizerCommitRootHandleKind, granted_access: u32) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(b"vrcforge-authority-finalizer-commit-root-access-v1\0");
    digest.update([match kind {
        FinalizerCommitRootHandleKind::ParentNamespace => 1,
        FinalizerCommitRootHandleKind::TransactionRoot => 2,
        FinalizerCommitRootHandleKind::TransactionProgressRoot => 3,
        FinalizerCommitRootHandleKind::TransactionNamespaceRoot => 4,
    }]);
    digest.update(granted_access.to_be_bytes());
    digest.finalize().into()
}

fn handle_granted_access(handle: &impl AsRawHandle) -> Result<u32, AuthorityMaintenanceError> {
    let mut information = unsafe { zeroed::<PublicObjectBasicInformation>() };
    let mut returned = 0u32;
    let status = unsafe {
        NtQueryObject(
            handle.as_raw_handle().cast(),
            ObjectBasicInformation,
            (&mut information as *mut PublicObjectBasicInformation).cast(),
            size_of::<PublicObjectBasicInformation>() as u32,
            &mut returned,
        )
    };
    if status < 0 || returned < (size_of::<u32>() * 2) as u32 {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_handle_access_unavailable",
        ));
    }
    Ok(information.granted_access)
}

fn privileged_complete_security_sha256(
    handle: &OwnedHandle,
) -> Result<[u8; 32], AuthorityMaintenanceError> {
    with_finalizer_security_privilege(|| complete_security_sha256(handle))
        .map_err(|error| AuthorityMaintenanceError(error.code()))
}

fn restricted_complete_security_sha256(
    handle: &impl AsRawHandle,
) -> Result<[u8; 32], AuthorityMaintenanceError> {
    complete_security_sha256(handle).map_err(|error| AuthorityMaintenanceError(error.code()))
}

fn complete_security_sha256(handle: &impl AsRawHandle) -> Result<[u8; 32], FinalizerSecurityError> {
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
        return Err(FinalizerSecurityError::new(
            "authority_finalizer_commit_security_readback_failed",
        ));
    }
    let descriptor = LocalSecurityDescriptor(descriptor);
    security_descriptor_sha256(descriptor.as_ptr())
}

#[cfg(test)]
fn ordinary_user_security_sha256(
    handle: &OwnedHandle,
) -> Result<[u8; 32], AuthorityMaintenanceError> {
    let information =
        OWNER_SECURITY_INFORMATION | GROUP_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION;
    let mut descriptor = ptr::null_mut();
    let status = unsafe {
        GetSecurityInfo(
            handle.as_raw_handle().cast(),
            SE_FILE_OBJECT,
            information,
            ptr::null_mut(),
            ptr::null_mut(),
            ptr::null_mut(),
            ptr::null_mut(),
            &mut descriptor,
        )
    };
    if status != 0 || descriptor.is_null() {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_test_security_readback_failed",
        ));
    }
    let descriptor = LocalSecurityDescriptor(descriptor);
    security_descriptor_sha256_with_information(descriptor.as_ptr(), information)
        .map_err(|error| AuthorityMaintenanceError(error.code()))
}

fn canonical_security_sha256(value: &str) -> Result<[u8; 32], AuthorityMaintenanceError> {
    let descriptor = LocalSecurityDescriptor::from_sddl(value)?;
    security_descriptor_sha256(descriptor.as_ptr())
        .map_err(|error| AuthorityMaintenanceError(error.code()))
}

fn security_descriptor_sha256(
    descriptor: PSECURITY_DESCRIPTOR,
) -> Result<[u8; 32], FinalizerSecurityError> {
    security_descriptor_sha256_with_information(descriptor, FULL_SECURITY_INFORMATION)
}

fn security_descriptor_sha256_with_information(
    descriptor: PSECURITY_DESCRIPTOR,
    security_information: u32,
) -> Result<[u8; 32], FinalizerSecurityError> {
    let mut encoded = ptr::null_mut();
    let mut length = 0u32;
    if unsafe {
        ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor,
            SDDL_REVISION_1,
            security_information,
            &mut encoded,
            &mut length,
        )
    } == 0
        || encoded.is_null()
        || length == 0
        || length > 16 * 1024
    {
        return Err(FinalizerSecurityError::new(
            "authority_finalizer_commit_security_readback_failed",
        ));
    }
    let words = unsafe { slice::from_raw_parts(encoded, length as usize) };
    let canonical = String::from_utf16(words).map_err(|_| {
        FinalizerSecurityError::new("authority_finalizer_commit_security_readback_failed")
    });
    unsafe {
        LocalFree(encoded.cast());
    }
    let canonical = canonical?;
    Ok(Sha256::digest(canonical.as_bytes()).into())
}

fn canonical_handle_path(handle: &impl AsRawHandle) -> Result<String, AuthorityMaintenanceError> {
    let mut buffer = vec![0u16; MAX_CANONICAL_PATH_WORDS];
    let length = unsafe {
        GetFinalPathNameByHandleW(
            handle.as_raw_handle().cast(),
            buffer.as_mut_ptr(),
            buffer.len() as u32,
            0,
        )
    };
    if length == 0 || length as usize >= buffer.len() {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_canonical_path_unavailable",
        ));
    }
    let path = String::from_utf16(&buffer[..length as usize]).map_err(|_| {
        AuthorityMaintenanceError("authority_finalizer_commit_canonical_path_invalid")
    })?;
    let normalized = normalize_canonical_path_text(&path)?;
    if !canonical_path_is_local(&normalized) {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_canonical_path_nonlocal",
        ));
    }
    Ok(normalized)
}

fn normalize_expected_canonical_path(path: &Path) -> Result<String, AuthorityMaintenanceError> {
    if !path.is_absolute()
        || path
            .components()
            .any(|component| matches!(component, Component::CurDir | Component::ParentDir))
        || !path
            .file_name()
            .and_then(|value| value.to_str())
            .is_some_and(|value| value.eq_ignore_ascii_case("finalizer-commits"))
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_parent_path_invalid",
        ));
    }
    let value = path.to_str().ok_or(AuthorityMaintenanceError(
        "authority_finalizer_commit_parent_path_invalid",
    ))?;
    let normalized = normalize_canonical_path_text(value)?;
    if !canonical_path_is_local(&normalized) {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_canonical_path_nonlocal",
        ));
    }
    Ok(normalized)
}

fn normalize_expected_layout_path(path: &Path) -> Result<String, AuthorityMaintenanceError> {
    if !path.is_absolute()
        || path
            .components()
            .any(|component| matches!(component, Component::CurDir | Component::ParentDir))
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_layout_path_invalid",
        ));
    }
    let value = path.to_str().ok_or(AuthorityMaintenanceError(
        "authority_finalizer_commit_layout_path_invalid",
    ))?;
    let normalized = normalize_canonical_path_text(value)?;
    if !canonical_path_is_local(&normalized) {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_canonical_path_nonlocal",
        ));
    }
    Ok(normalized)
}

fn normalize_canonical_path_text(value: &str) -> Result<String, AuthorityMaintenanceError> {
    if value.is_empty() || value.contains('\0') || value.starts_with(r"\\?\UNC\") {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_canonical_path_invalid",
        ));
    }
    let value = value.strip_prefix(r"\\?\").unwrap_or(value);
    let value = value.replace('/', "\\");
    if value.starts_with(r"\\")
        || value.len() < 3
        || value.as_bytes()[1] != b':'
        || value.as_bytes()[2] != b'\\'
        || !value.as_bytes()[0].is_ascii_alphabetic()
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_canonical_path_invalid",
        ));
    }
    let value = value.trim_end_matches('\\');
    if value.len() <= 2
        || value[3..]
            .split('\\')
            .any(|part| part.is_empty() || part == "." || part == "..")
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_canonical_path_invalid",
        ));
    }
    Ok(value.to_lowercase())
}

fn canonical_path_is_local(value: &str) -> bool {
    let encoded = value
        .encode_utf16()
        .chain(std::iter::once(0))
        .collect::<Vec<_>>();
    let mut root = vec![0u16; MAX_CANONICAL_PATH_WORDS];
    unsafe {
        GetVolumePathNameW(encoded.as_ptr(), root.as_mut_ptr(), root.len() as u32) != 0
            && drive_type_is_fixed(GetDriveTypeW(root.as_ptr()))
    }
}

fn drive_type_is_fixed(drive_type: u32) -> bool {
    drive_type == DRIVE_FIXED
}

fn canonical_path_sha256(value: &str) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(b"vrcforge-authority-finalizer-commit-canonical-path-v1\0");
    digest.update(value.as_bytes());
    digest.finalize().into()
}

fn protected_blob_canonical_path_sha256(value: &str) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(PROTECTED_BLOB_NAMESPACE_CANONICAL_PATH_DOMAIN);
    let folded = value.to_ascii_lowercase();
    digest.update((folded.len() as u64).to_be_bytes());
    digest.update(folded.as_bytes());
    digest.finalize().into()
}

fn receipt_canonical_path(
    parent_canonical_path: &str,
    name: &str,
) -> Result<String, AuthorityMaintenanceError> {
    validate_relative_name(name)?;
    if parent_canonical_path.is_empty() || parent_canonical_path.ends_with('\\') {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_canonical_path_invalid",
        ));
    }
    Ok(format!("{parent_canonical_path}\\{name}"))
}

fn hex_lower(value: &[u8; 32]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(64);
    for byte in value {
        output.push(HEX[(byte >> 4) as usize] as char);
        output.push(HEX[(byte & 0x0f) as usize] as char);
    }
    output
}

fn recovered_exit_ready_tip(
    recovered: &RecoveredFinalizerCommitState,
) -> Result<PersistedReceiptFileReference, AuthorityMaintenanceError> {
    let tip = *recovered
        .files()
        .get(stage_index(FinalizerCommitStage::ExitReady))
        .ok_or(AuthorityMaintenanceError(
            "authority_finalizer_commit_elevated_exit_tip_missing",
        ))?;
    if tip.stage() != FinalizerCommitStage::ExitReady {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_elevated_exit_tip_invalid",
        ));
    }
    tip.validate()?;
    Ok(tip)
}

fn duplicate_current_process_handle(
    source: &OwnedHandle,
) -> Result<OwnedHandle, AuthorityMaintenanceError> {
    let current = unsafe { GetCurrentProcess() };
    let mut duplicated = ptr::null_mut();
    if unsafe {
        DuplicateHandle(
            current,
            source.as_raw_handle().cast(),
            current,
            &mut duplicated,
            0,
            0,
            DUPLICATE_SAME_ACCESS,
        )
    } == 0
        || duplicated.is_null()
        || duplicated == INVALID_HANDLE_VALUE
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_elevated_process_duplicate_failed",
        ));
    }
    Ok(unsafe { OwnedHandle::from_raw_handle(duplicated as RawHandle) })
}

fn reopen_finalizer_image_handle(
    source: &OwnedHandle,
) -> Result<OwnedHandle, AuthorityMaintenanceError> {
    let source_identity = native_file_identity(source)?;
    let reopened = unsafe {
        ReOpenFile(
            source.as_raw_handle().cast(),
            FILE_READ_DATA | FILE_READ_ATTRIBUTES | FILE_READ_EA | READ_CONTROL | SYNCHRONIZE,
            FILE_SHARE_READ,
            FILE_FLAG_OPEN_REPARSE_POINT,
        )
    };
    if reopened.is_null() || reopened == INVALID_HANDLE_VALUE {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_elevated_image_reopen_failed",
        ));
    }
    let reopened = unsafe { OwnedHandle::from_raw_handle(reopened as RawHandle) };
    if native_file_identity(&reopened)? != source_identity {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_elevated_image_identity_mismatch",
        ));
    }
    Ok(reopened)
}

fn current_process_epoch(process: &OwnedHandle) -> Result<(u32, u64), AuthorityMaintenanceError> {
    let process_id = unsafe { GetProcessId(process.as_raw_handle().cast()) };
    if process_id == 0
        || process_id != unsafe { GetCurrentProcessId() }
        || unsafe { WaitForSingleObject(process.as_raw_handle().cast(), 0) } != WAIT_TIMEOUT
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_elevated_process_not_current",
        ));
    }
    let mut creation = FILETIME {
        dwLowDateTime: 0,
        dwHighDateTime: 0,
    };
    let mut exit = creation;
    let mut kernel = creation;
    let mut user = creation;
    if unsafe {
        GetProcessTimes(
            process.as_raw_handle().cast(),
            &mut creation,
            &mut exit,
            &mut kernel,
            &mut user,
        )
    } == 0
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_elevated_process_times_unavailable",
        ));
    }
    let creation_time =
        (u64::from(creation.dwHighDateTime) << 32) | u64::from(creation.dwLowDateTime);
    if creation_time == 0 {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_elevated_process_times_unavailable",
        ));
    }
    Ok((process_id, creation_time))
}

fn read_finalizer_image_stable(
    image: &OwnedHandle,
) -> Result<(NativeFileIdentity, [u8; 32]), AuthorityMaintenanceError> {
    let before = native_file_identity(image)?;
    if before.volume_serial == 0
        || before.file_id.iter().all(|byte| *byte == 0)
        || before.byte_length == 0
        || before.byte_length > MAX_FINALIZER_IMAGE_BYTES
        || before.link_count != 1
        || before.attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT) != 0
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_elevated_image_invalid",
        ));
    }
    seek(image, 0)?;
    let mut digest = Sha256::new();
    let mut remaining = before.byte_length;
    let mut buffer = [0u8; 64 * 1024];
    while remaining != 0 {
        let requested = usize::try_from(remaining.min(buffer.len() as u64)).map_err(|_| {
            AuthorityMaintenanceError("authority_finalizer_commit_elevated_image_read_failed")
        })?;
        let mut read = 0u32;
        if unsafe {
            ReadFile(
                image.as_raw_handle().cast(),
                buffer.as_mut_ptr(),
                requested as u32,
                &mut read,
                ptr::null_mut(),
            )
        } == 0
            || read == 0
            || read as usize > requested
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_elevated_image_read_failed",
            ));
        }
        digest.update(&buffer[..read as usize]);
        remaining = remaining
            .checked_sub(u64::from(read))
            .ok_or(AuthorityMaintenanceError(
                "authority_finalizer_commit_elevated_image_read_failed",
            ))?;
    }
    let mut extra = 0u8;
    let mut extra_read = 0u32;
    if unsafe {
        ReadFile(
            image.as_raw_handle().cast(),
            &mut extra,
            1,
            &mut extra_read,
            ptr::null_mut(),
        )
    } == 0
        || extra_read != 0
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_elevated_image_length_mismatch",
        ));
    }
    let after = native_file_identity(image)?;
    seek(image, 0)?;
    if before != after {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_elevated_image_drift",
        ));
    }
    let image_sha256: [u8; 32] = digest.finalize().into();
    if is_zero(&image_sha256) {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_elevated_image_digest_invalid",
        ));
    }
    Ok((before, image_sha256))
}

fn write_all(file: &OwnedHandle, bytes: &[u8]) -> Result<(), AuthorityMaintenanceError> {
    seek(file, 0)?;
    let mut offset = 0usize;
    while offset < bytes.len() {
        let mut written = 0u32;
        let chunk = &bytes[offset..];
        if unsafe {
            WriteFile(
                file.as_raw_handle().cast(),
                chunk.as_ptr(),
                chunk.len().min(u32::MAX as usize) as u32,
                &mut written,
                ptr::null_mut(),
            )
        } == 0
            || written == 0
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_receipt_write_failed",
            ));
        }
        offset = offset
            .checked_add(written as usize)
            .ok_or(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_receipt_write_failed",
            ))?;
    }
    Ok(())
}

fn read_bounded_stable(
    file: &OwnedHandle,
) -> Result<(Vec<u8>, NativeFileIdentity, [u8; 32]), AuthorityMaintenanceError> {
    let before = native_file_identity(file)?;
    verify_receipt_identity(&before, before.byte_length)?;
    if before.byte_length == 0 || before.byte_length > MAX_FINALIZER_COMMIT_RECEIPT_BYTES as u64 {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_store_receipt_size_invalid",
        ));
    }
    seek(file, 0)?;
    let mut bytes = vec![0u8; before.byte_length as usize];
    let mut offset = 0usize;
    while offset < bytes.len() {
        let mut read = 0u32;
        let remaining = &mut bytes[offset..];
        if unsafe {
            ReadFile(
                file.as_raw_handle().cast(),
                remaining.as_mut_ptr(),
                remaining.len().min(u32::MAX as usize) as u32,
                &mut read,
                ptr::null_mut(),
            )
        } == 0
            || read == 0
        {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_receipt_read_failed",
            ));
        }
        offset = offset
            .checked_add(read as usize)
            .ok_or(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_receipt_read_failed",
            ))?;
    }
    let mut extra = [0u8; 1];
    let mut extra_read = 0u32;
    if unsafe {
        ReadFile(
            file.as_raw_handle().cast(),
            extra.as_mut_ptr(),
            1,
            &mut extra_read,
            ptr::null_mut(),
        )
    } == 0
        || extra_read != 0
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_store_receipt_length_mismatch",
        ));
    }
    let after = native_file_identity(file)?;
    if before != after {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_store_receipt_identity_drift",
        ));
    }
    let digest = Sha256::digest(&bytes).into();
    if is_zero(&digest) {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_store_receipt_digest_invalid",
        ));
    }
    Ok((bytes, before, digest))
}

fn native_file_identity(
    handle: &impl AsRawHandle,
) -> Result<NativeFileIdentity, AuthorityMaintenanceError> {
    let mut information = unsafe { zeroed::<BY_HANDLE_FILE_INFORMATION>() };
    if unsafe { GetFileInformationByHandle(handle.as_raw_handle().cast(), &mut information) } == 0
        || information.dwVolumeSerialNumber == 0
        || (information.nFileIndexHigh == 0 && information.nFileIndexLow == 0)
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_store_file_identity_unavailable",
        ));
    }
    let file_index =
        (u64::from(information.nFileIndexHigh) << 32) | u64::from(information.nFileIndexLow);
    let mut file_id = [0u8; 16];
    file_id[..8].copy_from_slice(&file_index.to_be_bytes());
    Ok(NativeFileIdentity {
        volume_serial: u64::from(information.dwVolumeSerialNumber),
        file_id,
        byte_length: (u64::from(information.nFileSizeHigh) << 32)
            | u64::from(information.nFileSizeLow),
        link_count: information.nNumberOfLinks,
        attributes: information.dwFileAttributes,
    })
}

fn verify_root_identity(identity: &NativeFileIdentity) -> Result<(), AuthorityMaintenanceError> {
    if identity.attributes & FILE_ATTRIBUTE_DIRECTORY == 0
        || identity.attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
        || identity.link_count != 1
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_store_root_identity_invalid",
        ));
    }
    Ok(())
}

fn same_root_identity(left: &NativeFileIdentity, right: &NativeFileIdentity) -> bool {
    left.volume_serial == right.volume_serial
        && left.file_id == right.file_id
        && left.attributes == right.attributes
        && left.attributes & FILE_ATTRIBUTE_DIRECTORY != 0
}

fn validate_shared_transaction_root_readbacks(
    receipt_root: &AuthenticatedFinalizerCommitRootReadback,
    progress_root: &AuthenticatedFinalizerCommitRootReadback,
    expected_canonical_path_sha256: [u8; 32],
) -> Result<(), AuthorityMaintenanceError> {
    if !same_root_identity(&receipt_root.identity, &progress_root.identity)
        || receipt_root.canonical_path_readback_sha256 != expected_canonical_path_sha256
        || progress_root.canonical_path_readback_sha256 != expected_canonical_path_sha256
        || receipt_root.complete_security_readback_sha256
            != progress_root.complete_security_readback_sha256
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_transaction_progress_root_identity_mismatch",
        ));
    }
    Ok(())
}

fn same_authenticated_root_readback(
    left: &AuthenticatedFinalizerCommitRootReadback,
    right: &AuthenticatedFinalizerCommitRootReadback,
) -> bool {
    same_root_identity(&left.identity, &right.identity)
        && left.canonical_path_readback_sha256 == right.canonical_path_readback_sha256
        && left.complete_security_readback_sha256 == right.complete_security_readback_sha256
        && left.granted_access_readback_sha256 == right.granted_access_readback_sha256
}

fn verify_publishable_identity(
    identity: &NativeFileIdentity,
) -> Result<(), AuthorityMaintenanceError> {
    if identity.link_count != 1
        || identity.attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT) != 0
        || identity.byte_length > MAX_FINALIZER_COMMIT_RECEIPT_BYTES as u64
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_store_publishing_identity_invalid",
        ));
    }
    Ok(())
}

fn verify_receipt_identity(
    identity: &NativeFileIdentity,
    expected_length: u64,
) -> Result<(), AuthorityMaintenanceError> {
    if identity.byte_length != expected_length
        || identity.link_count != 1
        || identity.attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT) != 0
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_store_file_identity_invalid",
        ));
    }
    Ok(())
}

fn seek(file: &OwnedHandle, offset: i64) -> Result<(), AuthorityMaintenanceError> {
    let mut new_position = 0i64;
    if unsafe {
        SetFilePointerEx(
            file.as_raw_handle().cast(),
            offset,
            &mut new_position,
            FILE_BEGIN,
        )
    } == 0
        || new_position != offset
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_store_receipt_seek_failed",
        ));
    }
    Ok(())
}

fn flush_handle(handle: &OwnedHandle, code: &'static str) -> Result<(), AuthorityMaintenanceError> {
    let mut status = unsafe { zeroed::<IO_STATUS_BLOCK>() };
    if unsafe { NtFlushBuffersFile(handle.as_raw_handle().cast(), &mut status) } < 0 {
        return Err(AuthorityMaintenanceError(code));
    }
    Ok(())
}

fn wide_null(path: &Path) -> Vec<u16> {
    path.as_os_str()
        .encode_wide()
        .chain(std::iter::once(0))
        .collect()
}

fn path_is_local(path: &Path) -> bool {
    const DRIVE_REMOTE: u32 = 4;
    let encoded = wide_null(path);
    // Keep the maximum Win32 path scratch space off the relatively small
    // Windows test/service thread stack. The buffer is bounded and exists only
    // for this volume-root query.
    let mut root = vec![0u16; 32_768];
    unsafe {
        GetVolumePathNameW(encoded.as_ptr(), root.as_mut_ptr(), root.len() as u32) != 0
            && GetDriveTypeW(root.as_ptr()) != DRIVE_REMOTE
    }
}

fn validate_relative_name(name: &str) -> Result<(), AuthorityMaintenanceError> {
    if name.is_empty()
        || name == "."
        || name == ".."
        || name.contains(['/', '\\', ':', '\0'])
        || !name.is_ascii()
    {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_store_name_invalid",
        ));
    }
    Ok(())
}

fn stage_file_name(stage: FinalizerCommitStage) -> &'static str {
    match stage {
        FinalizerCommitStage::TransactionStarted => "00-transaction-started.receipt.json",
        FinalizerCommitStage::ApplyReady => "01-apply-ready.receipt.json",
        FinalizerCommitStage::SealReady => "02-seal-ready.receipt.json",
        FinalizerCommitStage::ExitReady => "03-exit-ready.receipt.json",
        FinalizerCommitStage::SealComplete => "04-seal-complete.receipt.json",
        FinalizerCommitStage::FinalCommit => "05-final-commit.receipt.json",
    }
}

pub(super) fn is_typed_finalizer_commit_namespace_name(name: &str) -> bool {
    if STAGES.iter().copied().any(|stage| {
        let final_name = stage_file_name(stage);
        name == final_name || name == publishing_name(final_name)
    }) {
        return true;
    }
    #[cfg(test)]
    {
        if [
            SEAL_INTENT_FILE_NAME,
            WORKER_SEAL_PROGRESS_FILE_NAME,
            CANDIDATE_SEAL_PROGRESS_FILE_NAME,
        ]
        .iter()
        .any(|final_name| name == *final_name || name == publishing_name(final_name))
        {
            return true;
        }
    }
    false
}

fn stage_index(stage: FinalizerCommitStage) -> usize {
    match stage {
        FinalizerCommitStage::TransactionStarted => 0,
        FinalizerCommitStage::ApplyReady => 1,
        FinalizerCommitStage::SealReady => 2,
        FinalizerCommitStage::ExitReady => 3,
        FinalizerCommitStage::SealComplete => 4,
        FinalizerCommitStage::FinalCommit => 5,
    }
}

fn stage_predecessor(stage: FinalizerCommitStage) -> Option<FinalizerCommitStage> {
    match stage {
        FinalizerCommitStage::TransactionStarted => None,
        FinalizerCommitStage::ApplyReady => Some(FinalizerCommitStage::TransactionStarted),
        FinalizerCommitStage::SealReady => Some(FinalizerCommitStage::ApplyReady),
        FinalizerCommitStage::ExitReady => Some(FinalizerCommitStage::SealReady),
        FinalizerCommitStage::SealComplete => Some(FinalizerCommitStage::ExitReady),
        FinalizerCommitStage::FinalCommit => Some(FinalizerCommitStage::SealComplete),
    }
}

fn security_expectation(
    latest_stage: FinalizerCommitStage,
) -> FinalizerArtifactSecurityExpectation {
    match latest_stage {
        FinalizerCommitStage::TransactionStarted
        | FinalizerCommitStage::ApplyReady
        | FinalizerCommitStage::SealReady => FinalizerArtifactSecurityExpectation {
            phase: FinalizerArtifactSecurityPhase::Staging,
            worker_nonce: FinalizerArtifactDescriptorConstraint::StagingOnly,
            candidate_consumption: FinalizerArtifactDescriptorConstraint::StagingOnly,
        },
        FinalizerCommitStage::ExitReady => FinalizerArtifactSecurityExpectation {
            phase: FinalizerArtifactSecurityPhase::SealInProgress,
            worker_nonce: FinalizerArtifactDescriptorConstraint::StagingOrSealed,
            candidate_consumption: FinalizerArtifactDescriptorConstraint::StagingOrSealed,
        },
        FinalizerCommitStage::SealComplete | FinalizerCommitStage::FinalCommit => {
            FinalizerArtifactSecurityExpectation {
                phase: FinalizerArtifactSecurityPhase::Sealed,
                worker_nonce: FinalizerArtifactDescriptorConstraint::SealedOnly,
                candidate_consumption: FinalizerArtifactDescriptorConstraint::SealedOnly,
            }
        }
    }
}

fn recovery_directive(latest_stage: FinalizerCommitStage) -> FinalizerCommitRecoveryDirective {
    match latest_stage {
        FinalizerCommitStage::TransactionStarted => {
            FinalizerCommitRecoveryDirective::ResumeSystemApply
        }
        FinalizerCommitStage::ApplyReady => {
            FinalizerCommitRecoveryDirective::ResumeCandidateActivation
        }
        FinalizerCommitStage::SealReady => FinalizerCommitRecoveryDirective::ResumeSystemExit,
        FinalizerCommitStage::ExitReady => {
            FinalizerCommitRecoveryDirective::RecoverGenerationSealTerminalAuthorization
        }
        FinalizerCommitStage::SealComplete => {
            FinalizerCommitRecoveryDirective::CompleteActiveHeadRuntimeAndPersistFinalCommit
        }
        FinalizerCommitStage::FinalCommit => {
            FinalizerCommitRecoveryDirective::ReadOnlyVerifyFinalCommitAndRuntime
        }
    }
}

fn constraint_accepts(
    constraint: FinalizerArtifactDescriptorConstraint,
    observed: FinalizerArtifactSecurityPhase,
) -> bool {
    match (constraint, observed) {
        (
            FinalizerArtifactDescriptorConstraint::StagingOnly,
            FinalizerArtifactSecurityPhase::Staging,
        )
        | (
            FinalizerArtifactDescriptorConstraint::StagingOrSealed,
            FinalizerArtifactSecurityPhase::Staging | FinalizerArtifactSecurityPhase::Sealed,
        )
        | (
            FinalizerArtifactDescriptorConstraint::SealedOnly,
            FinalizerArtifactSecurityPhase::Sealed,
        ) => true,
        _ => false,
    }
}

fn authenticated_root_digest(readback: &AuthenticatedFinalizerCommitRootReadback) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(b"vrcforge-authority-finalizer-commit-store-root-v2\0");
    digest.update(readback.identity.volume_serial.to_be_bytes());
    digest.update(readback.identity.file_id);
    digest.update(readback.canonical_path_readback_sha256);
    digest.update(readback.complete_security_readback_sha256);
    digest.finalize().into()
}

fn published_receipt_reference_security_sha256(
    identity: &NativeFileIdentity,
    canonical_path: &str,
) -> Result<[u8; 32], AuthorityMaintenanceError> {
    verify_publishable_identity(identity)?;
    let mut digest = Sha256::new();
    digest.update(b"vrcforge-authority-finalizer-commit-published-reference-v2\0");
    digest.update(identity.volume_serial.to_be_bytes());
    digest.update(identity.file_id);
    digest.update(identity.byte_length.to_be_bytes());
    digest.update(identity.link_count.to_be_bytes());
    digest.update(identity.attributes.to_be_bytes());
    digest.update(canonical_path_sha256(canonical_path));
    digest.update(canonical_security_sha256(
        FINALIZER_COMMIT_RECEIPT_IMMUTABLE_SDDL,
    )?);
    digest.update([1]);
    Ok(digest.finalize().into())
}

#[cfg(test)]
fn test_root_digest(identity: &NativeFileIdentity) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(b"vrcforge-authority-finalizer-commit-store-test-root-v1\0");
    digest.update(identity.volume_serial.to_be_bytes());
    digest.update(identity.file_id);
    digest.finalize().into()
}

#[cfg(test)]
fn unsecured_test_root_readback(
    handle: &OwnedHandle,
    kind: FinalizerCommitRootHandleKind,
) -> Result<AuthenticatedFinalizerCommitRootReadback, AuthorityMaintenanceError> {
    let identity = native_file_identity(handle)?;
    verify_root_identity(&identity)?;
    let granted_access = handle_granted_access(handle)?;
    if granted_access != ROOT_ACCESS {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_test_root_access_invalid",
        ));
    }
    Ok(AuthenticatedFinalizerCommitRootReadback {
        identity,
        canonical_path_readback_sha256: canonical_path_sha256(&canonical_handle_path(handle)?),
        complete_security_readback_sha256: ordinary_user_security_sha256(handle)?,
        granted_access_readback_sha256: granted_access_sha256(kind, granted_access),
    })
}

fn verify_receipt_capability(
    handle: &OwnedHandle,
    kind: FinalizerCommitReceiptHandleKind,
    reverify: Option<ReceiptReverify>,
    expected_canonical_path: &str,
) -> Result<[u8; 32], AuthorityMaintenanceError> {
    if canonical_handle_path(handle)? != expected_canonical_path {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_receipt_path_mismatch",
        ));
    }
    if let Some(reverify) = reverify {
        let digest = reverify(handle, kind, expected_canonical_path)?;
        if is_zero(&digest) {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_store_receipt_security_invalid",
            ));
        }
        return Ok(digest);
    }
    #[cfg(test)]
    {
        let identity = native_file_identity(handle)?;
        let mut digest = Sha256::new();
        digest.update(b"vrcforge-authority-finalizer-commit-store-test-security-v1\0");
        digest.update(identity.volume_serial.to_be_bytes());
        digest.update(identity.file_id);
        digest.update([match kind {
            FinalizerCommitReceiptHandleKind::PublishingInspectionStaging => 1,
            FinalizerCommitReceiptHandleKind::PublishingInspectionSealed => 2,
            FinalizerCommitReceiptHandleKind::PublishingCreate => 3,
            FinalizerCommitReceiptHandleKind::PublishingStagingRecovery => 4,
            FinalizerCommitReceiptHandleKind::PublishingCreateSealed => 5,
            FinalizerCommitReceiptHandleKind::PublishingStagingRecoverySealed => 6,
            FinalizerCommitReceiptHandleKind::PublishingRecoverySealed => 7,
            FinalizerCommitReceiptHandleKind::PublishedTightening => 8,
            FinalizerCommitReceiptHandleKind::PublishedReadOnly => 9,
            FinalizerCommitReceiptHandleKind::RestrictedPublishedReadOnly => 10,
        }]);
        return Ok(digest.finalize().into());
    }
    #[cfg(not(test))]
    Err(AuthorityMaintenanceError(
        "authority_finalizer_commit_store_receipt_authenticator_missing",
    ))
}

fn require_same_receipt_capability(
    handle: &OwnedHandle,
    kind: FinalizerCommitReceiptHandleKind,
    reverify: Option<ReceiptReverify>,
    expected: [u8; 32],
    expected_canonical_path: &str,
) -> Result<(), AuthorityMaintenanceError> {
    if verify_receipt_capability(handle, kind, reverify, expected_canonical_path)? != expected {
        return Err(AuthorityMaintenanceError(
            "authority_finalizer_commit_receipt_authentication_drift",
        ));
    }
    Ok(())
}

fn is_zero(value: &[u8; 32]) -> bool {
    value.iter().all(|byte| *byte == 0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::primitive_evidence_authority_install::bootstrap_activation::CandidateProcessEvidence;
    use crate::primitive_evidence_authority_install::finalizer_commit_protocol::{
        ActiveHeadCasDisposition, ActiveHeadCasReadback, ActiveHeadPriorReadback,
        ApplyReadyEvidence, CandidateActivationIdentity, CandidateStoppedReadback,
        CommittedRuntimeIdentity, ExactSealedSecurityReadback, ExactServiceProcessIdentity,
        ExactServiceRuntimeIdentity, ExitReadyEvidence, FinalCommitEvidence,
        FinalizerCommitPlanBinding, NonceArtifactPair, OperationResiduePlan,
        OperationZeroResidueReadback, ResidueDimension, ResidueObjectPlan, ResidueObjectReadback,
        SealReadyEvidence, TransactionStartedEvidence, WriterHandlesClosedReadback,
    };
    use crate::primitive_evidence_authority_install::finalizer_generation_seal::{
        GenerationSealBinding, GenerationSealTerminalAuthorization,
    };
    use crate::primitive_evidence_authority_install::AuthorityMaintenanceOperation;
    use std::{
        fs,
        sync::atomic::{AtomicU64, Ordering},
    };

    static NEXT_TEMP: AtomicU64 = AtomicU64::new(1);

    struct TempRoot(std::path::PathBuf);

    impl TempRoot {
        fn new(label: &str) -> Self {
            let sequence = NEXT_TEMP.fetch_add(1, Ordering::Relaxed);
            let root = std::env::temp_dir().join(format!(
                "vrcforge-finalizer-store-{label}-{}-{sequence}",
                std::process::id()
            ));
            fs::create_dir(&root).unwrap();
            Self(root)
        }
    }

    impl Drop for TempRoot {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    struct OrdinaryReceiptCapabilityReadback {
        identity: NativeFileIdentity,
        canonical_path_sha256: [u8; 32],
        security_sha256: [u8; 32],
        granted_access: u32,
        kind: FinalizerCommitReceiptHandleKind,
    }

    fn ordinary_root_readback(
        handle: &OwnedHandle,
        kind: FinalizerCommitRootHandleKind,
        expected_access: u32,
    ) -> Result<AuthenticatedFinalizerCommitRootReadback, AuthorityMaintenanceError> {
        let identity = native_file_identity(handle)?;
        verify_root_identity(&identity)?;
        let granted_access = handle_granted_access(handle)?;
        if granted_access != expected_access {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_test_root_access_invalid",
            ));
        }
        Ok(AuthenticatedFinalizerCommitRootReadback {
            identity,
            canonical_path_readback_sha256: canonical_path_sha256(&canonical_handle_path(handle)?),
            complete_security_readback_sha256: ordinary_user_security_sha256(handle)?,
            granted_access_readback_sha256: granted_access_sha256(kind, granted_access),
        })
    }

    fn ordinary_receipt_readback(
        handle: &OwnedHandle,
        kind: FinalizerCommitReceiptHandleKind,
        expected_access: u32,
    ) -> Result<OrdinaryReceiptCapabilityReadback, AuthorityMaintenanceError> {
        let identity = native_file_identity(handle)?;
        verify_publishable_identity(&identity)?;
        let granted_access = handle_granted_access(handle)?;
        if granted_access != expected_access {
            return Err(AuthorityMaintenanceError(
                "authority_finalizer_commit_test_receipt_access_invalid",
            ));
        }
        Ok(OrdinaryReceiptCapabilityReadback {
            identity,
            canonical_path_sha256: canonical_path_sha256(&canonical_handle_path(handle)?),
            security_sha256: ordinary_user_security_sha256(handle)?,
            granted_access,
            kind,
        })
    }

    fn protect_directory_dacl_for_test(path: &Path) {
        let handle = open_directory_for_test(path, READ_CONTROL | WRITE_DAC).unwrap();
        let mut descriptor = ptr::null_mut();
        let status = unsafe {
            GetSecurityInfo(
                handle.as_raw_handle().cast(),
                SE_FILE_OBJECT,
                DACL_SECURITY_INFORMATION,
                ptr::null_mut(),
                ptr::null_mut(),
                ptr::null_mut(),
                ptr::null_mut(),
                &mut descriptor,
            )
        };
        assert_eq!(status, 0);
        assert!(!descriptor.is_null());
        let descriptor = LocalSecurityDescriptor(descriptor);
        let mut present = 0;
        let mut defaulted = 0;
        let mut dacl = ptr::null_mut();
        assert_ne!(
            unsafe {
                GetSecurityDescriptorDacl(
                    descriptor.as_ptr(),
                    &mut present,
                    &mut dacl,
                    &mut defaulted,
                )
            },
            0
        );
        assert_ne!(present, 0);
        assert!(!dacl.is_null());
        assert_eq!(
            unsafe {
                SetSecurityInfo(
                    handle.as_raw_handle().cast(),
                    SE_FILE_OBJECT,
                    DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
                    ptr::null_mut(),
                    ptr::null_mut(),
                    dacl,
                    ptr::null_mut(),
                )
            },
            0
        );
    }

    fn residue_dimensions() -> [ResidueDimension; 13] {
        [
            ResidueDimension::MaintenanceService,
            ResidueDimension::TransientStaging,
            ResidueDimension::CandidateActivationCredential,
            ResidueDimension::MaintenancePipe,
            ResidueDimension::WorkerProcessAndState,
            ResidueDimension::WorkerNonce,
            ResidueDimension::CandidateConsumption,
            ResidueDimension::FinalizerReceiptPublishing,
            ResidueDimension::ActiveHead,
            ResidueDimension::RetirementStaging,
            ResidueDimension::RetirementAborted,
            ResidueDimension::RetirementFinal,
            ResidueDimension::FinalizerCommitStore,
        ]
    }

    fn residue_object_plan(index: usize) -> ResidueObjectPlan {
        let dimension = residue_dimensions()[index];
        let binding_byte = 0x80_u8.saturating_add(index as u8);
        if matches!(
            dimension,
            ResidueDimension::WorkerNonce
                | ResidueDimension::CandidateConsumption
                | ResidueDimension::ActiveHead
                | ResidueDimension::FinalizerCommitStore
        ) {
            ResidueObjectPlan::present_exact(
                dimension,
                [binding_byte; 32],
                [0xa0_u8.saturating_add(index as u8); 32],
            )
            .unwrap()
        } else {
            ResidueObjectPlan::absent(dimension, [binding_byte; 32]).unwrap()
        }
    }

    fn residue_plan() -> OperationResiduePlan {
        OperationResiduePlan::new(
            AuthorityMaintenanceOperation::Install,
            std::array::from_fn(residue_object_plan),
        )
        .unwrap()
    }

    fn zero_residue(kernel_seed: u8) -> OperationZeroResidueReadback {
        let plan = residue_plan();
        let objects = std::array::from_fn(|index| {
            let object = residue_object_plan(index);
            let kernel_readback = [kernel_seed.saturating_add(index as u8); 32];
            if matches!(
                residue_dimensions()[index],
                ResidueDimension::WorkerNonce
                    | ResidueDimension::CandidateConsumption
                    | ResidueDimension::ActiveHead
                    | ResidueDimension::FinalizerCommitStore
            ) {
                ResidueObjectReadback::present_exact(
                    object,
                    [0xa0_u8.saturating_add(index as u8); 32],
                    kernel_readback,
                )
                .unwrap()
            } else {
                ResidueObjectReadback::absent(object, kernel_readback).unwrap()
            }
        });
        OperationZeroResidueReadback::new(plan, objects).unwrap()
    }

    fn plan_binding() -> FinalizerCommitPlanBinding {
        FinalizerCommitPlanBinding::new(
            AuthorityMaintenanceOperation::Install,
            [0x70; 32],
            [0x71; 32],
            [0x54; 32],
            [0x5a; 32],
            [0x61; 32],
            [0x62; 32],
            [0x64; 32],
            4,
            [0x58; 32],
            residue_plan(),
        )
        .unwrap()
    }

    fn binding_with_parts(
        authenticated_root_sha256: [u8; 32],
        capsule_sha256: [u8; 32],
        plan_sha256: [u8; 32],
        generation_sha256: [u8; 32],
        transaction_sha256: [u8; 32],
    ) -> FinalizerCommitBinding {
        FinalizerCommitBinding::new(
            capsule_sha256,
            plan_sha256,
            generation_sha256,
            transaction_sha256,
            plan_binding(),
            authenticated_root_sha256,
        )
        .unwrap()
    }

    fn binding_for_root_with_parts(
        root: &Path,
        capsule_sha256: [u8; 32],
        plan_sha256: [u8; 32],
        generation_sha256: [u8; 32],
        transaction_sha256: [u8; 32],
    ) -> FinalizerCommitBinding {
        let held_root = open_root_for_test(root).unwrap();
        let root_identity = native_file_identity(&held_root).unwrap();
        let authenticated_root_sha256 = test_root_digest(&root_identity);
        binding_with_parts(
            authenticated_root_sha256,
            capsule_sha256,
            plan_sha256,
            generation_sha256,
            transaction_sha256,
        )
    }

    fn binding_for_root(root: &Path) -> FinalizerCommitBinding {
        binding_for_root_with_parts(root, [0x11; 32], [0x12; 32], [0x13; 32], [0x14; 32])
    }

    fn artifacts() -> NonceArtifactPair {
        NonceArtifactPair::new(
            DurableFileIdentity::new(0x101, [0x21; 16], 1, 121, [0x31; 32]).unwrap(),
            DurableFileIdentity::new(0x102, [0x22; 16], 1, 122, [0x32; 32]).unwrap(),
        )
        .unwrap()
    }

    fn worker() -> ExactServiceProcessIdentity {
        ExactServiceProcessIdentity::new([0x70; 32], 2222, 555, [0x71; 32]).unwrap()
    }

    fn service_process(process_id: u32, creation_time: u64) -> CandidateProcessEvidence {
        CandidateProcessEvidence::from_held_process(
            process_id,
            creation_time,
            [0x5a; 32],
            0x2000,
            0x3030,
            [0x31; 16],
            1,
            0x20,
        )
        .unwrap()
    }

    fn candidate_runtime() -> ExactServiceRuntimeIdentity {
        ExactServiceRuntimeIdentity::from_observed(
            [0x54; 32],
            [0x55; 32],
            service_process(3333, 666),
        )
        .unwrap()
    }

    fn committed_runtime() -> ExactServiceRuntimeIdentity {
        ExactServiceRuntimeIdentity::from_observed(
            [0x54; 32],
            [0x58; 32],
            service_process(4242, 777),
        )
        .unwrap()
    }

    fn seal_ready(binding: FinalizerCommitBinding) -> SealReadyEvidence {
        SealReadyEvidence::new(
            artifacts(),
            WriterHandlesClosedReadback::new(worker(), true, [0x41; 32], true, [0x42; 32]).unwrap(),
            CandidateActivationIdentity::new(
                binding,
                artifacts(),
                [0x54; 32],
                [0x55; 32],
                service_process(3333, 666),
                [0x5b; 32],
            )
            .unwrap(),
        )
        .unwrap()
    }

    fn sealed_security() -> ExactSealedSecurityReadback {
        ExactSealedSecurityReadback::new(
            [0x51; 32], [0x51; 32], [0x51; 32], true, true, true, [0x53; 32],
        )
        .unwrap()
    }

    fn candidate_stopped() -> CandidateStoppedReadback {
        CandidateStoppedReadback::exact_stopped(
            candidate_runtime().exact_runtime_instance_sha256(),
            3333,
            666,
            [0x5a; 32],
            [0x42; 32],
            [0x56; 32],
            [0x57; 32],
        )
        .unwrap()
    }

    fn generation_authorization(
        binding: FinalizerCommitBinding,
        seed: u8,
    ) -> GenerationSealTerminalAuthorization {
        generation_authorization_with_final_roots(binding, seed, [seed.wrapping_add(7); 32])
    }

    fn generation_authorization_with_final_roots(
        binding: FinalizerCommitBinding,
        seed: u8,
        final_root_capabilities_sha256: [u8; 32],
    ) -> GenerationSealTerminalAuthorization {
        let digest = |offset: u8| [seed.wrapping_add(offset); 32];
        GenerationSealTerminalAuthorization::exact_test_fixture(
            GenerationSealBinding::from_commit_binding(binding).unwrap(),
            digest(0),
            digest(1),
            digest(2),
            digest(3),
            binding.final_commit_store_root_identity_sha256(),
            digest(5),
            digest(6),
            final_root_capabilities_sha256,
        )
        .unwrap()
    }

    fn final_commit(binding: FinalizerCommitBinding) -> FinalCommitEvidence {
        final_commit_with_zero_residue(binding, 0xc0)
    }

    fn final_commit_with_zero_residue(
        binding: FinalizerCommitBinding,
        kernel_seed: u8,
    ) -> FinalCommitEvidence {
        let active_head = ActiveHeadCasReadback::new(
            ActiveHeadPriorReadback::present([0x61; 32], [0x61; 32]).unwrap(),
            [0x62; 32],
            [0x62; 32],
            binding.generation_sha256(),
            [0x64; 32],
            4,
            ActiveHeadCasDisposition::Applied,
            [0x63; 32],
        )
        .unwrap();
        FinalCommitEvidence::new_with_runner_policy_sealed_identity(
            active_head,
            CommittedRuntimeIdentity::new(
                active_head,
                committed_runtime(),
                [0x66; 16],
                [0x67; 32],
                [0x68; 32],
                [0x6a; 32],
                binding.expected_final_commit_gate_sha256(),
            )
            .unwrap(),
            RunnerPolicySealedIdentity::exact_test_fixture(0x6b),
            zero_residue(kernel_seed),
        )
        .unwrap()
    }

    fn states_and_writes(
        binding: FinalizerCommitBinding,
    ) -> Vec<(FinalizerCommitProtocolState, Option<DurableReceiptWrite>)> {
        let mut state = FinalizerCommitProtocolState::transaction_started(
            binding,
            TransactionStartedEvidence::new(binding, worker(), [0x72; 32]).unwrap(),
        )
        .unwrap();
        let mut result = vec![(state.clone(), None)];
        let write = state
            .system_actor()
            .record_apply_ready(ApplyReadyEvidence::new([0x72; 32]).unwrap())
            .unwrap();
        result.push((state.clone(), Some(write)));
        let write = state
            .system_actor()
            .record_seal_ready(seal_ready(binding))
            .unwrap();
        result.push((state.clone(), Some(write)));
        let write = state
            .system_actor()
            .record_exit_ready(ExitReadyEvidence::new(worker(), [0x41; 32], [0x74; 32]).unwrap())
            .unwrap();
        result.push((state.clone(), Some(write)));
        let generation_authorization = generation_authorization(binding, 0x5d);
        let write = state
            .elevated_finalizer()
            .record_seal_complete_authorized(
                &generation_authorization,
                artifacts(),
                sealed_security(),
                candidate_stopped(),
            )
            .unwrap();
        result.push((state.clone(), Some(write)));
        let write = state
            .elevated_finalizer()
            .record_final_commit(final_commit(binding))
            .unwrap();
        result.push((state, Some(write)));
        result
    }

    fn seal_intent_evidence() -> FinalizerSealIntentEvidence {
        let artifacts = artifacts();
        FinalizerSealIntentEvidence::new(
            artifacts.worker_nonce(),
            artifacts.candidate_consumption(),
            [0x81; 32],
            [0x82; 32],
            [0x51; 32],
        )
        .unwrap()
    }

    fn seal_progress_evidence(artifact: FinalizerSealArtifact) -> FinalizerSealProgressEvidence {
        let artifacts = artifacts();
        let identity = match artifact {
            FinalizerSealArtifact::WorkerNonce => artifacts.worker_nonce(),
            FinalizerSealArtifact::CandidateConsumption => artifacts.candidate_consumption(),
        };
        FinalizerSealProgressEvidence::new(artifact, identity, [0x51; 32]).unwrap()
    }

    fn persist(
        store: &FinalizerCommitReceiptStore,
        state: &FinalizerCommitProtocolState,
        write: Option<&DurableReceiptWrite>,
    ) -> Result<PersistedFinalizerCommitStage, AuthorityMaintenanceError> {
        match state.latest_stage() {
            FinalizerCommitStage::TransactionStarted => {
                assert!(write.is_none());
                store.persist_transaction_started(state)
            }
            FinalizerCommitStage::ApplyReady
            | FinalizerCommitStage::SealReady
            | FinalizerCommitStage::ExitReady => {
                store.persist_system_transition(state, write.unwrap())
            }
            FinalizerCommitStage::SealComplete => {
                let projection = state.seal_complete_persistence_projection()?.ok_or(
                    AuthorityMaintenanceError(
                        "authority_finalizer_seal_complete_authorization_invalid",
                    ),
                )?;
                let root = Path::new(&store.root_canonical_path);
                for sequence in 0..=projection.terminal_sequence() {
                    let name =
                        generation_progress_relative_name(&projection.manifest_sha256(), sequence);
                    let path = root.join(name);
                    if !path.exists() {
                        fs::write(path, format!("test-generation-progress-{sequence}")).map_err(
                            |_| {
                                AuthorityMaintenanceError(
                                    "authority_finalizer_commit_test_progress_write_failed",
                                )
                            },
                        )?;
                    }
                }
                let generation_authorization = generation_authorization(store.binding, 0x5d);
                let authorization =
                    SealCompletePersistenceAuthorization::from_generation_seal_authorization(
                        &generation_authorization,
                    )?;
                store.persist_seal_complete(state, write.unwrap(), &authorization)
            }
            FinalizerCommitStage::FinalCommit => {
                let recovered = store.recover()?.ok_or(AuthorityMaintenanceError(
                    "authority_finalizer_commit_store_receipt_missing",
                ))?;
                let seal_complete_tip = *recovered
                    .files()
                    .get(stage_index(FinalizerCommitStage::SealComplete))
                    .ok_or(AuthorityMaintenanceError(
                        "authority_finalizer_final_commit_seal_complete_missing",
                    ))?;
                let authorization = FinalCommitPersistenceAuthorization::for_test(
                    store.binding,
                    store.authenticated_root_sha256,
                    store.final_commit_gate_sha256,
                    seal_complete_tip,
                    state,
                )?;
                store.persist_final_commit(state, write.unwrap(), &authorization)
            }
        }
    }

    fn directory_snapshot(root: &Path) -> Vec<(String, Vec<u8>)> {
        let mut snapshot = fs::read_dir(root)
            .unwrap()
            .map(|entry| {
                let entry = entry.unwrap();
                (
                    entry.file_name().to_string_lossy().into_owned(),
                    fs::read(entry.path()).unwrap(),
                )
            })
            .collect::<Vec<_>>();
        snapshot.sort_by(|left, right| left.0.cmp(&right.0));
        snapshot
    }

    fn assert_stage_unpublished(root: &Path, stage: FinalizerCommitStage) {
        let final_name = stage_file_name(stage);
        assert!(!root.join(final_name).exists());
        assert!(!root.join(publishing_name(final_name)).exists());
    }

    #[test]
    fn transaction_root_composition_rejects_split_identity_and_case_sensitive_namespaces() {
        validate_case_sensitivity_flags(0).unwrap();
        assert_eq!(
            validate_case_sensitivity_flags(FILE_CS_FLAG_CASE_SENSITIVE_DIR)
                .unwrap_err()
                .code(),
            "authority_finalizer_commit_root_case_sensitive"
        );
        assert_eq!(
            validate_case_sensitivity_flags(2).unwrap_err().code(),
            "authority_finalizer_commit_root_case_sensitivity_flags_invalid"
        );

        let identity = NativeFileIdentity {
            volume_serial: 7,
            file_id: [8; 16],
            byte_length: 0,
            link_count: 1,
            attributes: FILE_ATTRIBUTE_DIRECTORY,
        };
        let expected_path = [9; 32];
        let receipt = AuthenticatedFinalizerCommitRootReadback {
            identity,
            canonical_path_readback_sha256: expected_path,
            complete_security_readback_sha256: [10; 32],
            granted_access_readback_sha256: [11; 32],
        };
        let progress = AuthenticatedFinalizerCommitRootReadback {
            granted_access_readback_sha256: [12; 32],
            ..receipt
        };
        validate_shared_transaction_root_readbacks(&receipt, &progress, expected_path).unwrap();

        let mut split_identity = progress;
        split_identity.identity.file_id = [13; 16];
        assert_eq!(
            validate_shared_transaction_root_readbacks(&receipt, &split_identity, expected_path,)
                .unwrap_err()
                .code(),
            "authority_finalizer_commit_transaction_progress_root_identity_mismatch"
        );

        let mut path_alias = progress;
        path_alias.canonical_path_readback_sha256 = [14; 32];
        assert!(
            validate_shared_transaction_root_readbacks(&receipt, &path_alias, expected_path)
                .is_err()
        );

        let mut security_drift = progress;
        security_drift.complete_security_readback_sha256 = [15; 32];
        assert!(validate_shared_transaction_root_readbacks(
            &receipt,
            &security_drift,
            expected_path
        )
        .is_err());
    }

    #[test]
    fn ordinary_user_held_parent_child_and_receipt_roles_are_exact() {
        let root = TempRoot::new("held-capabilities");
        let parent_path = root.0.join("finalizer-commits");
        fs::create_dir(&parent_path).unwrap();
        let transaction_sha256 = [0xa1; 32];
        let transaction_name = hex_lower(&transaction_sha256);
        let transaction_path = parent_path.join(&transaction_name);
        fs::create_dir(&transaction_path).unwrap();

        let parent_access = AUTHENTICATED_PARENT_ROOT_ACCESS & !ACCESS_SYSTEM_SECURITY;
        let transaction_access = AUTHENTICATED_TRANSACTION_ROOT_ACCESS & !ACCESS_SYSTEM_SECURITY;
        let receipt_create_access = AUTHENTICATED_RECEIPT_CREATE_ACCESS & !ACCESS_SYSTEM_SECURITY;
        let receipt_read_access = AUTHENTICATED_RECEIPT_READ_ACCESS & !ACCESS_SYSTEM_SECURITY;
        let parent = open_directory_for_test(&parent_path, parent_access).unwrap();
        let parent_readback = ordinary_root_readback(
            &parent,
            FinalizerCommitRootHandleKind::ParentNamespace,
            parent_access,
        )
        .unwrap();
        let normalized_parent = normalize_expected_canonical_path(&parent_path).unwrap();
        assert_eq!(
            parent_readback.canonical_path_readback_sha256,
            canonical_path_sha256(&normalized_parent)
        );

        let transaction =
            nt_open_relative_directory(&parent, &transaction_name, transaction_access)
                .unwrap()
                .unwrap();
        let transaction_readback = ordinary_root_readback(
            &transaction,
            FinalizerCommitRootHandleKind::TransactionRoot,
            transaction_access,
        )
        .unwrap();
        assert_eq!(
            transaction_readback.canonical_path_readback_sha256,
            canonical_path_sha256(&format!("{normalized_parent}\\{transaction_name}"))
        );
        assert_eq!(
            ordinary_root_readback(
                &parent,
                FinalizerCommitRootHandleKind::ParentNamespace,
                parent_access,
            )
            .unwrap(),
            parent_readback
        );

        let final_name = "capability.receipt.json";
        let private_name = publishing_name(final_name);
        let publishing = nt_open_relative(
            &transaction,
            &private_name,
            FILE_CREATE,
            receipt_create_access,
            None,
        )
        .unwrap()
        .unwrap();
        let before_write = ordinary_receipt_readback(
            &publishing,
            FinalizerCommitReceiptHandleKind::PublishingCreate,
            receipt_create_access,
        )
        .unwrap();
        let bytes = b"held-relative-capability";
        write_all(&publishing, bytes).unwrap();
        flush_handle(
            &publishing,
            "authority_finalizer_commit_test_receipt_flush_failed",
        )
        .unwrap();
        let after_write = ordinary_receipt_readback(
            &publishing,
            FinalizerCommitReceiptHandleKind::PublishingCreate,
            receipt_create_access,
        )
        .unwrap();
        assert_eq!(
            before_write.identity.volume_serial,
            after_write.identity.volume_serial
        );
        assert_eq!(before_write.identity.file_id, after_write.identity.file_id);
        assert_eq!(
            before_write.identity.link_count,
            after_write.identity.link_count
        );
        assert_eq!(
            before_write.identity.attributes,
            after_write.identity.attributes
        );
        assert_eq!(before_write.security_sha256, after_write.security_sha256);
        assert_eq!(before_write.granted_access, after_write.granted_access);
        assert_eq!(
            before_write.canonical_path_sha256,
            after_write.canonical_path_sha256
        );
        assert_eq!(
            rename_relative_no_replace(&publishing, &transaction, final_name).unwrap(),
            RenameDisposition::Published
        );
        drop(publishing);
        flush_handle(
            &transaction,
            "authority_finalizer_commit_test_parent_flush_failed",
        )
        .unwrap();

        let published = nt_open_relative(
            &transaction,
            final_name,
            FILE_OPEN,
            receipt_read_access,
            None,
        )
        .unwrap()
        .unwrap();
        let published_readback = ordinary_receipt_readback(
            &published,
            FinalizerCommitReceiptHandleKind::PublishedReadOnly,
            receipt_read_access,
        )
        .unwrap();
        assert_eq!(
            published_readback.identity.file_id,
            after_write.identity.file_id
        );
        assert_eq!(
            published_readback.security_sha256,
            after_write.security_sha256
        );
        assert_ne!(
            published_readback.granted_access,
            after_write.granted_access
        );
        assert_eq!(
            ordinary_receipt_readback(
                &published,
                FinalizerCommitReceiptHandleKind::PublishingStagingRecovery,
                receipt_create_access,
            )
            .unwrap_err()
            .code(),
            "authority_finalizer_commit_test_receipt_access_invalid"
        );
        let mut written = 0u32;
        assert_eq!(
            unsafe {
                WriteFile(
                    published.as_raw_handle().cast(),
                    b"x".as_ptr(),
                    1,
                    &mut written,
                    ptr::null_mut(),
                )
            },
            0
        );
        assert_eq!(written, 0);
        assert_eq!(fs::read(transaction_path.join(final_name)).unwrap(), bytes);
    }

    #[test]
    fn ordinary_user_authentication_detects_replacement_permission_drift_and_hardlinks() {
        let root = TempRoot::new("held-hostile");
        let parent_path = root.0.join("finalizer-commits");
        fs::create_dir(&parent_path).unwrap();
        let parent_access = AUTHENTICATED_PARENT_ROOT_ACCESS & !ACCESS_SYSTEM_SECURITY;
        let transaction_access = AUTHENTICATED_TRANSACTION_ROOT_ACCESS & !ACCESS_SYSTEM_SECURITY;
        let receipt_read_access = AUTHENTICATED_RECEIPT_READ_ACCESS & !ACCESS_SYSTEM_SECURITY;
        let parent = open_directory_for_test(&parent_path, parent_access).unwrap();
        assert_eq!(
            normalize_expected_canonical_path(&root.0)
                .unwrap_err()
                .code(),
            "authority_finalizer_commit_parent_path_invalid"
        );

        let replacement_name = hex_lower(&[0xb1; 32]);
        let replacement_path = parent_path.join(&replacement_name);
        fs::create_dir(&replacement_path).unwrap();
        let replacement_handle =
            nt_open_relative_directory(&parent, &replacement_name, transaction_access)
                .unwrap()
                .unwrap();
        let replacement_initial = ordinary_root_readback(
            &replacement_handle,
            FinalizerCommitRootHandleKind::TransactionRoot,
            transaction_access,
        )
        .unwrap();
        let moved_path = parent_path.join("moved-transaction-root");
        fs::rename(&replacement_path, &moved_path).unwrap();
        fs::create_dir(&replacement_path).unwrap();
        let replacement_after = ordinary_root_readback(
            &replacement_handle,
            FinalizerCommitRootHandleKind::TransactionRoot,
            transaction_access,
        )
        .unwrap();
        assert_eq!(replacement_after.identity, replacement_initial.identity);
        assert_ne!(
            replacement_after.canonical_path_readback_sha256,
            replacement_initial.canonical_path_readback_sha256
        );

        let drift_name = hex_lower(&[0xb2; 32]);
        let drift_path = parent_path.join(&drift_name);
        fs::create_dir(&drift_path).unwrap();
        let drift_handle = nt_open_relative_directory(&parent, &drift_name, transaction_access)
            .unwrap()
            .unwrap();
        let drift_initial = ordinary_root_readback(
            &drift_handle,
            FinalizerCommitRootHandleKind::TransactionRoot,
            transaction_access,
        )
        .unwrap();
        protect_directory_dacl_for_test(&drift_path);
        let drift_after = ordinary_root_readback(
            &drift_handle,
            FinalizerCommitRootHandleKind::TransactionRoot,
            transaction_access,
        )
        .unwrap();
        assert_eq!(drift_after.identity, drift_initial.identity);
        assert_ne!(
            drift_after.complete_security_readback_sha256,
            drift_initial.complete_security_readback_sha256
        );

        let linked_name = "linked.receipt.json";
        let linked_path = drift_path.join(linked_name);
        fs::write(&linked_path, b"linked-receipt").unwrap();
        fs::hard_link(&linked_path, drift_path.join("second-link.receipt.json")).unwrap();
        let linked = nt_open_relative(
            &drift_handle,
            linked_name,
            FILE_OPEN,
            receipt_read_access,
            None,
        )
        .unwrap()
        .unwrap();
        assert_eq!(
            ordinary_receipt_readback(
                &linked,
                FinalizerCommitReceiptHandleKind::PublishedReadOnly,
                receipt_read_access,
            )
            .unwrap_err()
            .code(),
            "authority_finalizer_commit_store_publishing_identity_invalid"
        );
    }

    #[test]
    fn immutable_receipt_policy_rejects_staging_acl_and_mutating_published_handles() {
        let staging = canonical_security_sha256(STATE_STAGING_SDDL).unwrap();
        let private_sealed =
            canonical_security_sha256(FINALIZER_PUBLISHING_PRIVATE_SEALED_SDDL).unwrap();
        let immutable = canonical_security_sha256(FINALIZER_COMMIT_RECEIPT_IMMUTABLE_SDDL).unwrap();
        assert_ne!(staging, private_sealed);
        assert_ne!(private_sealed, immutable);
        assert_ne!(staging, immutable);
        assert!(FINALIZER_PUBLISHING_PRIVATE_SEALED_SDDL.contains("(A;;0x00170089;;;BA)"));
        assert!(FINALIZER_COMMIT_RECEIPT_IMMUTABLE_SDDL.contains("(A;;0x00120089;;;BA)"));
        assert!(!FINALIZER_COMMIT_RECEIPT_IMMUTABLE_SDDL.contains("(A;;0x00130089;;;BA)"));
        assert!(FINALIZER_COMMIT_TRANSACTION_ROOT_SDDL.contains("(A;;0x001200ab;;;SY)"));
        assert!(FINALIZER_COMMIT_TRANSACTION_ROOT_SDDL.contains("(A;;0x001200ab;;;BA)"));
        assert_eq!(
            AUTHENTICATED_TRANSACTION_ROOT_ACCESS,
            FINALIZER_COMMIT_TRANSACTION_RECEIPT_HANDLE_ACCESS
        );
        assert_eq!(
            AUTHENTICATED_TRANSACTION_PROGRESS_ROOT_ACCESS,
            FINALIZER_COMMIT_TRANSACTION_PROGRESS_HANDLE_ACCESS
        );
        assert_eq!(
            AUTHENTICATED_TRANSACTION_ROOT_ACCESS & !AUTHENTICATED_TRANSACTION_PROGRESS_ROOT_ACCESS,
            0
        );
        assert_eq!(
            AUTHENTICATED_TRANSACTION_ROOT_ACCESS & (FILE_LIST_DIRECTORY | FILE_READ_EA),
            0
        );
        assert_eq!(AUTHENTICATED_TRANSACTION_ROOT_ACCESS & 0x40, 0);

        validate_receipt_capability_observation(
            FinalizerCommitReceiptHandleKind::PublishedReadOnly,
            AUTHENTICATED_RECEIPT_READ_ACCESS,
            immutable,
        )
        .unwrap();
        assert_eq!(
            validate_receipt_capability_observation(
                FinalizerCommitReceiptHandleKind::PublishedReadOnly,
                AUTHENTICATED_RECEIPT_READ_ACCESS,
                staging,
            )
            .unwrap_err()
            .code(),
            "authority_finalizer_commit_receipt_security_invalid"
        );
        for hostile in [FILE_WRITE_DATA, DELETE, WRITE_DAC] {
            assert_eq!(
                validate_receipt_capability_observation(
                    FinalizerCommitReceiptHandleKind::PublishedReadOnly,
                    AUTHENTICATED_RECEIPT_READ_ACCESS | hostile,
                    immutable,
                )
                .unwrap_err()
                .code(),
                "authority_finalizer_commit_receipt_access_invalid"
            );
        }
        validate_receipt_capability_observation(
            FinalizerCommitReceiptHandleKind::PublishingRecoverySealed,
            AUTHENTICATED_RECEIPT_SEALED_RECOVERY_ACCESS,
            private_sealed,
        )
        .unwrap();
        assert_eq!(
            AUTHENTICATED_RECEIPT_SEALED_RECOVERY_ACCESS & DELETE,
            DELETE
        );
        assert_eq!(
            AUTHENTICATED_RECEIPT_SEALED_RECOVERY_ACCESS & WRITE_DAC,
            WRITE_DAC
        );
        for hostile in [FILE_WRITE_DATA] {
            assert!(validate_receipt_capability_observation(
                FinalizerCommitReceiptHandleKind::PublishingRecoverySealed,
                AUTHENTICATED_RECEIPT_SEALED_RECOVERY_ACCESS | hostile,
                private_sealed,
            )
            .is_err());
        }
        validate_receipt_capability_observation(
            FinalizerCommitReceiptHandleKind::PublishedTightening,
            AUTHENTICATED_RECEIPT_SEALED_RECOVERY_ACCESS,
            immutable,
        )
        .unwrap();
    }

    #[test]
    fn private_sealed_crash_cuts_before_or_after_rename_require_exact_typed_roll_forward() {
        let staging = canonical_security_sha256(STATE_STAGING_SDDL).unwrap();
        let private_sealed =
            canonical_security_sha256(FINALIZER_PUBLISHING_PRIVATE_SEALED_SDDL).unwrap();
        let immutable = canonical_security_sha256(FINALIZER_COMMIT_RECEIPT_IMMUTABLE_SDDL).unwrap();
        assert_eq!(
            classify_publishing_security(staging, staging, private_sealed).unwrap(),
            PublishingSecurityPhase::Staging
        );
        assert_eq!(
            classify_publishing_security(private_sealed, staging, private_sealed).unwrap(),
            PublishingSecurityPhase::PrivateSealed
        );
        assert_eq!(
            classify_publishing_security(immutable, staging, private_sealed)
                .unwrap_err()
                .code(),
            "authority_finalizer_commit_receipt_recovery_security_invalid"
        );
        assert_eq!(
            classify_final_receipt_security(private_sealed).unwrap(),
            FinalReceiptSecurityPhase::PrivateSealed
        );
        assert_eq!(
            classify_final_receipt_security(immutable).unwrap(),
            FinalReceiptSecurityPhase::PublishedImmutable
        );
        assert_eq!(
            classify_final_receipt_security(staging).unwrap_err().code(),
            "authority_finalizer_commit_final_receipt_security_invalid"
        );

        let root = TempRoot::new("sealed-cut-bytes");
        let binding = binding_for_root(&root.0);
        let store = FinalizerCommitReceiptStore::open_unsecured_test(&root.0, binding).unwrap();
        let state = &states_and_writes(binding)[0].0;
        let bytes =
            DurableFinalizerCommitEnvelope::new(state, store.authenticated_root_sha256, None)
                .unwrap()
                .canonical_json()
                .unwrap();
        DurableFinalizerCommitEnvelope::parse_canonical(
            &bytes,
            binding,
            store.authenticated_root_sha256,
            FinalizerCommitStage::TransactionStarted,
            None,
        )
        .unwrap();
        let digest: [u8; 32] = Sha256::digest(&bytes).into();
        validate_collision_readback(&bytes, digest, &bytes).unwrap();
        assert_eq!(
            validate_collision_readback(b"hostile", Sha256::digest(b"hostile").into(), &bytes)
                .unwrap_err()
                .code(),
            "authority_finalizer_commit_store_collision_conflict"
        );
    }

    #[test]
    fn every_crash_boundary_recovers_from_receipts_not_journal_markers() {
        let root = TempRoot::new("crash-boundaries");
        let binding = binding_for_root(&root.0);
        let cases = states_and_writes(binding);
        let expected = [
            (
                FinalizerArtifactSecurityPhase::Staging,
                FinalizerCommitRecoveryDirective::ResumeSystemApply,
            ),
            (
                FinalizerArtifactSecurityPhase::Staging,
                FinalizerCommitRecoveryDirective::ResumeCandidateActivation,
            ),
            (
                FinalizerArtifactSecurityPhase::Staging,
                FinalizerCommitRecoveryDirective::ResumeSystemExit,
            ),
            (
                FinalizerArtifactSecurityPhase::SealInProgress,
                FinalizerCommitRecoveryDirective::RecoverGenerationSealTerminalAuthorization,
            ),
            (
                FinalizerArtifactSecurityPhase::Sealed,
                FinalizerCommitRecoveryDirective::CompleteActiveHeadRuntimeAndPersistFinalCommit,
            ),
            (
                FinalizerArtifactSecurityPhase::Sealed,
                FinalizerCommitRecoveryDirective::ReadOnlyVerifyFinalCommitAndRuntime,
            ),
        ];

        for (index, (state, write)) in cases.iter().enumerate() {
            let store = FinalizerCommitReceiptStore::open_unsecured_test(&root.0, binding).unwrap();
            let created = persist(&store, state, write.as_ref()).unwrap();
            assert_eq!(created.disposition(), ProtocolWriteDisposition::Created);
            drop(store);

            // Both a missing StepCompleted append and a stale StepStarted append
            // reopen to the same receipt-authoritative directive.
            for _journal_window in ["before-step-completed", "after-step-started"] {
                let restarted =
                    FinalizerCommitReceiptStore::open_unsecured_test(&root.0, binding).unwrap();
                let recovered = restarted.recover().unwrap().unwrap();
                assert_eq!(recovered.protocol_state(), state);
                assert_eq!(recovered.files().len(), index + 1);
                assert_eq!(recovered.security().phase(), expected[index].0);
                assert_eq!(recovered.directive(), expected[index].1);
            }

            let store = FinalizerCommitReceiptStore::open_unsecured_test(&root.0, binding).unwrap();
            let replay = persist(&store, state, write.as_ref()).unwrap();
            assert_eq!(
                replay.disposition(),
                ProtocolWriteDisposition::AlreadyIdentical
            );
            assert_eq!(replay.file(), created.file());
        }
    }

    #[test]
    fn seal_boundary_never_accepts_the_opposite_descriptor_phase() {
        let root = TempRoot::new("seal-boundary");
        let binding = binding_for_root(&root.0);
        let cases = states_and_writes(binding);
        let store = FinalizerCommitReceiptStore::open_unsecured_test(&root.0, binding).unwrap();
        for (state, write) in &cases[..3] {
            persist(&store, state, write.as_ref()).unwrap();
            let expectation = store.recover().unwrap().unwrap().security();
            assert_eq!(expectation.phase(), FinalizerArtifactSecurityPhase::Staging);
            expectation
                .require_observed_pair(
                    FinalizerArtifactSecurityPhase::Staging,
                    FinalizerArtifactSecurityPhase::Staging,
                )
                .unwrap();
            assert_eq!(
                expectation
                    .require_observed_pair(
                        FinalizerArtifactSecurityPhase::Sealed,
                        FinalizerArtifactSecurityPhase::Staging,
                    )
                    .unwrap_err()
                    .code(),
                "authority_finalizer_commit_security_phase_mismatch"
            );
            assert_eq!(
                expectation.worker_nonce(),
                FinalizerArtifactDescriptorConstraint::StagingOnly
            );
            assert_eq!(
                expectation.candidate_consumption(),
                FinalizerArtifactDescriptorConstraint::StagingOnly
            );
        }
        persist(&store, &cases[3].0, cases[3].1.as_ref()).unwrap();
        let expectation = store.recover().unwrap().unwrap().security();
        assert_eq!(
            expectation.phase(),
            FinalizerArtifactSecurityPhase::SealInProgress
        );
        assert_eq!(
            expectation.worker_nonce(),
            FinalizerArtifactDescriptorConstraint::StagingOrSealed
        );
        assert_eq!(
            expectation.candidate_consumption(),
            FinalizerArtifactDescriptorConstraint::StagingOrSealed
        );
        for (worker, candidate) in [
            (
                FinalizerArtifactSecurityPhase::Staging,
                FinalizerArtifactSecurityPhase::Staging,
            ),
            (
                FinalizerArtifactSecurityPhase::Sealed,
                FinalizerArtifactSecurityPhase::Staging,
            ),
            (
                FinalizerArtifactSecurityPhase::Staging,
                FinalizerArtifactSecurityPhase::Sealed,
            ),
            (
                FinalizerArtifactSecurityPhase::Sealed,
                FinalizerArtifactSecurityPhase::Sealed,
            ),
        ] {
            expectation
                .require_observed_pair(worker, candidate)
                .unwrap();
        }
        persist(&store, &cases[4].0, cases[4].1.as_ref()).unwrap();
        let expectation = store.recover().unwrap().unwrap().security();
        assert_eq!(expectation.phase(), FinalizerArtifactSecurityPhase::Sealed);
        expectation
            .require_observed_pair(
                FinalizerArtifactSecurityPhase::Sealed,
                FinalizerArtifactSecurityPhase::Sealed,
            )
            .unwrap();
        assert_eq!(
            expectation.worker_nonce(),
            FinalizerArtifactDescriptorConstraint::SealedOnly
        );
        assert_eq!(
            expectation.candidate_consumption(),
            FinalizerArtifactDescriptorConstraint::SealedOnly
        );
        assert!(expectation
            .require_observed_pair(
                FinalizerArtifactSecurityPhase::Staging,
                FinalizerArtifactSecurityPhase::Sealed,
            )
            .is_err());
    }

    #[test]
    fn conflicting_replay_gap_and_noncanonical_bytes_fail_closed() {
        let root = TempRoot::new("hostile");
        let binding = binding_for_root(&root.0);
        let cases = states_and_writes(binding);
        let store = FinalizerCommitReceiptStore::open_unsecured_test(&root.0, binding).unwrap();
        persist(&store, &cases[0].0, None).unwrap();

        let conflicting = FinalizerCommitProtocolState::transaction_started(
            binding,
            TransactionStartedEvidence::new(binding, worker(), [0x79; 32]).unwrap(),
        )
        .unwrap();
        assert_eq!(
            store
                .persist_transaction_started(&conflicting)
                .unwrap_err()
                .code(),
            "authority_finalizer_commit_store_receipt_conflict"
        );
        drop(store);

        let started_path = root
            .0
            .join(stage_file_name(FinalizerCommitStage::TransactionStarted));
        let mut noncanonical = fs::read(&started_path).unwrap();
        noncanonical.push(b'\n');
        fs::write(&started_path, noncanonical).unwrap();
        let reopened = FinalizerCommitReceiptStore::open_unsecured_test(&root.0, binding).unwrap();
        assert_eq!(
            reopened.recover().unwrap_err().code(),
            "authority_finalizer_commit_store_canonical_json_invalid"
        );

        let gap_root = TempRoot::new("gap");
        fs::write(
            gap_root
                .0
                .join(stage_file_name(FinalizerCommitStage::SealReady)),
            b"{}",
        )
        .unwrap();
        let gap_binding = binding_for_root(&gap_root.0);
        let gap_store =
            FinalizerCommitReceiptStore::open_unsecured_test(&gap_root.0, gap_binding).unwrap();
        assert_eq!(
            gap_store.recover().unwrap_err().code(),
            "authority_finalizer_commit_store_chain_gap"
        );
    }

    #[test]
    fn privileged_publication_edges_reject_actor_and_authorization_bypass_without_residue() {
        let root = TempRoot::new("privileged-edge-bypass");
        let binding = binding_for_root(&root.0);
        let cases = states_and_writes(binding);
        let store = FinalizerCommitReceiptStore::open_unsecured_test(&root.0, binding).unwrap();
        for (state, write) in &cases[..4] {
            persist(&store, state, write.as_ref()).unwrap();
        }

        for index in [4usize, 5usize] {
            assert_eq!(
                store
                    .persist_system_transition(&cases[index].0, cases[index].1.as_ref().unwrap())
                    .unwrap_err()
                    .code(),
                "authority_finalizer_commit_store_transition_actor_invalid"
            );
        }
        assert_stage_unpublished(&root.0, FinalizerCommitStage::SealComplete);
        assert_stage_unpublished(&root.0, FinalizerCommitStage::FinalCommit);

        let before_seal_rejection = directory_snapshot(&root.0);
        let final_root_drift = generation_authorization_with_final_roots(binding, 0x5d, [0x9f; 32]);
        let wrong_seal_authorization =
            SealCompletePersistenceAuthorization::from_generation_seal_authorization(
                &final_root_drift,
            )
            .unwrap();
        assert_eq!(
            store
                .persist_seal_complete(
                    &cases[4].0,
                    cases[4].1.as_ref().unwrap(),
                    &wrong_seal_authorization,
                )
                .unwrap_err()
                .code(),
            "authority_finalizer_seal_complete_authorization_mismatch"
        );
        assert_eq!(directory_snapshot(&root.0), before_seal_rejection);
        assert_stage_unpublished(&root.0, FinalizerCommitStage::SealComplete);

        persist(&store, &cases[4].0, cases[4].1.as_ref()).unwrap();
        let seal_complete_tip = store.recover().unwrap().unwrap().tip();
        let wrong_final_authorization = FinalCommitPersistenceAuthorization::for_test(
            binding,
            store.authenticated_root_sha256,
            store.final_commit_gate_sha256,
            seal_complete_tip,
            &cases[5].0,
        )
        .unwrap();
        let mut drifted_final_state = cases[4].0.clone();
        let drifted_final_write = drifted_final_state
            .elevated_finalizer()
            .record_final_commit(final_commit_with_zero_residue(binding, 0xd0))
            .unwrap();
        let before_final_rejection = directory_snapshot(&root.0);
        assert_eq!(
            store
                .persist_final_commit(
                    &drifted_final_state,
                    &drifted_final_write,
                    &wrong_final_authorization,
                )
                .unwrap_err()
                .code(),
            "authority_finalizer_final_commit_authorization_mismatch"
        );
        assert_eq!(directory_snapshot(&root.0), before_final_rejection);
        assert_stage_unpublished(&root.0, FinalizerCommitStage::FinalCommit);
    }

    #[test]
    fn predecessor_file_identity_and_exact_tip_are_restart_bound() {
        let root = TempRoot::new("identity");
        let binding = binding_for_root(&root.0);
        let cases = states_and_writes(binding);
        let store = FinalizerCommitReceiptStore::open_unsecured_test(&root.0, binding).unwrap();
        let first = persist(&store, &cases[0].0, None).unwrap();
        let second = persist(&store, &cases[1].0, cases[1].1.as_ref()).unwrap();
        let recovered = store.recover_exact_tip(second.file()).unwrap();
        assert_eq!(recovered.tip(), second.file());
        assert_ne!(first.file().identity(), second.file().identity());
        assert_ne!(
            first.file().receipt_sha256(),
            second.file().receipt_sha256()
        );
        assert_ne!(
            first.file().protocol_state_sha256(),
            second.file().protocol_state_sha256()
        );

        let wrong_tip = PersistedReceiptFileReference::new(
            second.file().stage(),
            first.file().identity(),
            second.file().receipt_sha256(),
            second.file().protocol_state_sha256(),
            second.file().security_readback_sha256(),
        )
        .unwrap();
        assert_eq!(
            store.recover_exact_tip(wrong_tip).unwrap_err().code(),
            "authority_finalizer_commit_store_tip_identity_mismatch"
        );

        drop(store);
        fs::remove_file(
            root.0
                .join(stage_file_name(FinalizerCommitStage::TransactionStarted)),
        )
        .unwrap();
        let restarted = FinalizerCommitReceiptStore::open_unsecured_test(&root.0, binding).unwrap();
        assert_eq!(
            restarted.recover().unwrap_err().code(),
            "authority_finalizer_commit_store_chain_gap"
        );
    }

    #[test]
    fn seal_complete_cannot_regress_while_a_later_commit_receipt_exists() {
        let root = TempRoot::new("sealed-regression");
        let binding = binding_for_root(&root.0);
        let cases = states_and_writes(binding);
        let store = FinalizerCommitReceiptStore::open_unsecured_test(&root.0, binding).unwrap();
        for (state, write) in &cases {
            persist(&store, state, write.as_ref()).unwrap();
        }
        drop(store);

        fs::remove_file(
            root.0
                .join(stage_file_name(FinalizerCommitStage::SealComplete)),
        )
        .unwrap();
        let restarted = FinalizerCommitReceiptStore::open_unsecured_test(&root.0, binding).unwrap();
        assert_eq!(
            restarted.recover().unwrap_err().code(),
            "authority_finalizer_commit_store_chain_gap"
        );
    }

    #[test]
    fn final_commit_write_window_has_distinct_pre_and_post_commit_recovery() {
        let root = TempRoot::new("final-commit-window");
        let binding = binding_for_root(&root.0);
        let cases = states_and_writes(binding);
        let store = FinalizerCommitReceiptStore::open_unsecured_test(&root.0, binding).unwrap();
        for (state, write) in &cases[..5] {
            persist(&store, state, write.as_ref()).unwrap();
        }

        // A crash before the create-new FinalCommit file remains a sealed
        // roll-forward. Generic rollback is not a valid recovery choice.
        assert_eq!(
            store.recover().unwrap().unwrap().directive(),
            FinalizerCommitRecoveryDirective::CompleteActiveHeadRuntimeAndPersistFinalCommit
        );

        persist(&store, &cases[5].0, cases[5].1.as_ref()).unwrap();
        drop(store);

        // A crash after the file write but before a non-authoritative journal
        // StepCompleted append permits read-only verification only. Cleanup or
        // any other transaction mutation would cross the terminal boundary.
        let restarted = FinalizerCommitReceiptStore::open_unsecured_test(&root.0, binding).unwrap();
        assert_eq!(
            restarted.recover().unwrap().unwrap().directive(),
            FinalizerCommitRecoveryDirective::ReadOnlyVerifyFinalCommitAndRuntime
        );
    }

    #[test]
    fn post_final_commit_recovery_is_read_only_even_with_private_residue() {
        let root = TempRoot::new("post-commit-read-only");
        let binding = binding_for_root(&root.0);
        let cases = states_and_writes(binding);
        let store = FinalizerCommitReceiptStore::open_unsecured_test(&root.0, binding).unwrap();
        for (state, write) in &cases {
            persist(&store, state, write.as_ref()).unwrap();
        }
        assert_eq!(
            store.recover().unwrap().unwrap().directive(),
            FinalizerCommitRecoveryDirective::ReadOnlyVerifyFinalCommitAndRuntime
        );
        let clean_snapshot = directory_snapshot(&root.0);
        assert!(clean_snapshot
            .iter()
            .all(|(name, _)| !name.ends_with(PRIVATE_PUBLISHING_SUFFIX)));
        assert_eq!(
            store.recover().unwrap().unwrap().tip().stage(),
            FinalizerCommitStage::FinalCommit
        );
        assert_eq!(directory_snapshot(&root.0), clean_snapshot);

        let injected_residue = root.0.join(publishing_name(WORKER_SEAL_PROGRESS_FILE_NAME));
        fs::write(&injected_residue, b"hostile-post-commit-residue").unwrap();
        let before_failure = directory_snapshot(&root.0);
        assert_eq!(
            store.recover().unwrap_err().code(),
            "authority_finalizer_commit_store_post_commit_publishing_residue"
        );
        assert_eq!(directory_snapshot(&root.0), before_failure);
        assert_eq!(
            fs::read(injected_residue).unwrap(),
            b"hostile-post-commit-residue"
        );
    }

    #[test]
    fn transaction_plan_and_generation_binding_mismatch_is_rejected() {
        let root = TempRoot::new("binding");
        let binding = binding_for_root(&root.0);
        let cases = states_and_writes(binding);
        let store = FinalizerCommitReceiptStore::open_unsecured_test(&root.0, binding).unwrap();
        persist(&store, &cases[0].0, None).unwrap();
        drop(store);

        for hostile in [
            binding_for_root_with_parts(&root.0, [0x11; 32], [0x91; 32], [0x13; 32], [0x14; 32]),
            binding_for_root_with_parts(&root.0, [0x11; 32], [0x12; 32], [0x92; 32], [0x14; 32]),
            binding_for_root_with_parts(&root.0, [0x11; 32], [0x12; 32], [0x13; 32], [0x93; 32]),
        ] {
            let restarted =
                FinalizerCommitReceiptStore::open_unsecured_test(&root.0, hostile).unwrap();
            assert_eq!(
                restarted.recover().unwrap_err().code(),
                "authority_finalizer_commit_store_binding_mismatch"
            );
        }
    }

    #[test]
    fn authenticated_root_digest_is_part_of_the_commit_binding() {
        let first = TempRoot::new("root-binding-first");
        let second = TempRoot::new("root-binding-second");
        let first_binding = binding_for_root(&first.0);
        let error = match FinalizerCommitReceiptStore::open_unsecured_test(&second.0, first_binding)
        {
            Ok(_) => panic!("mismatched authenticated root was accepted"),
            Err(error) => error,
        };
        assert_eq!(
            error.code(),
            "authority_finalizer_commit_store_root_binding_mismatch"
        );
        assert!(directory_snapshot(&second.0).is_empty());
        FinalizerCommitReceiptStore::open_unsecured_test(&first.0, first_binding).unwrap();
    }

    #[test]
    fn active_head_factory_self_authenticates_genesis_and_returns_only_typed_final_commit() {
        let root = TempRoot::new("active-head-final-commit");
        let binding = binding_for_root(&root.0);
        let cases = states_and_writes(binding);
        let store = FinalizerCommitReceiptStore::open_unsecured_test(&root.0, binding).unwrap();
        let genesis =
            DurableFinalizerCommitEnvelope::new(&cases[0].0, store.authenticated_root_sha256, None)
                .unwrap()
                .canonical_json()
                .unwrap();
        let self_authenticated =
            DurableFinalizerCommitEnvelope::parse_transaction_started_self_authenticated(
                &genesis,
                store.authenticated_root_sha256,
                binding.transaction_sha256(),
            )
            .unwrap();
        assert_eq!(self_authenticated.binding, binding);
        assert_eq!(
            DurableFinalizerCommitEnvelope::parse_transaction_started_self_authenticated(
                &genesis,
                store.authenticated_root_sha256,
                [0xe1; 32],
            )
            .unwrap_err()
            .code(),
            "authority_finalizer_commit_active_head_binding_mismatch"
        );
        assert_eq!(
            DurableFinalizerCommitEnvelope::parse_transaction_started_self_authenticated(
                &genesis,
                [0xe2; 32],
                binding.transaction_sha256(),
            )
            .unwrap_err()
            .code(),
            "authority_finalizer_commit_active_head_binding_mismatch"
        );

        persist(&store, &cases[0].0, None).unwrap();
        assert_eq!(
            store
                .recover_typed_final_commit(binding.transaction_sha256())
                .unwrap_err()
                .code(),
            "authority_finalizer_commit_terminal_readback_incomplete"
        );
        for (state, write) in &cases[1..] {
            persist(&store, state, write.as_ref()).unwrap();
        }
        let terminal = store
            .recover_typed_final_commit(binding.transaction_sha256())
            .unwrap();
        let expected_projection = cases
            .last()
            .unwrap()
            .0
            .final_commit_persistence_projection()
            .unwrap()
            .unwrap();
        assert_eq!(
            terminal.active_head_transaction_sha256(),
            binding.transaction_sha256()
        );
        assert_eq!(
            terminal.authenticated_root_sha256(),
            store.authenticated_root_sha256
        );
        assert_eq!(terminal.tip().stage(), FinalizerCommitStage::FinalCommit);
        assert_eq!(terminal.projection(), expected_projection);
        assert_eq!(
            store
                .recover_typed_final_commit([0xe3; 32])
                .unwrap_err()
                .code(),
            "authority_finalizer_commit_terminal_readback_binding_mismatch"
        );
    }

    #[test]
    fn legacy_seal_sidecars_never_authorize_or_redirect_terminal_recovery() {
        let root = TempRoot::new("mixed-seal");
        let binding = binding_for_root(&root.0);
        let cases = states_and_writes(binding);
        let store = FinalizerCommitReceiptStore::open_unsecured_test(&root.0, binding).unwrap();
        for (state, write) in &cases[..4] {
            persist(&store, state, write.as_ref()).unwrap();
        }

        let before_intent = store.recover().unwrap().unwrap();
        assert_eq!(
            before_intent.directive(),
            FinalizerCommitRecoveryDirective::RecoverGenerationSealTerminalAuthorization
        );
        assert_eq!(
            before_intent.security().phase(),
            FinalizerArtifactSecurityPhase::SealInProgress
        );

        store.persist_seal_intent(seal_intent_evidence()).unwrap();
        let after_intent = store.recover().unwrap().unwrap();
        assert_eq!(
            after_intent.directive(),
            FinalizerCommitRecoveryDirective::RecoverGenerationSealTerminalAuthorization
        );
        assert_eq!(
            after_intent.security().phase(),
            FinalizerArtifactSecurityPhase::SealInProgress
        );
        for observed in [
            (
                FinalizerArtifactSecurityPhase::Staging,
                FinalizerArtifactSecurityPhase::Staging,
            ),
            (
                FinalizerArtifactSecurityPhase::Sealed,
                FinalizerArtifactSecurityPhase::Staging,
            ),
            (
                FinalizerArtifactSecurityPhase::Staging,
                FinalizerArtifactSecurityPhase::Sealed,
            ),
            (
                FinalizerArtifactSecurityPhase::Sealed,
                FinalizerArtifactSecurityPhase::Sealed,
            ),
        ] {
            after_intent
                .security()
                .require_observed_pair(observed.0, observed.1)
                .unwrap();
        }

        store
            .persist_seal_progress(seal_progress_evidence(FinalizerSealArtifact::WorkerNonce))
            .unwrap();
        let worker_complete = store.recover().unwrap().unwrap();
        assert_eq!(
            worker_complete.directive(),
            FinalizerCommitRecoveryDirective::RecoverGenerationSealTerminalAuthorization
        );
        assert_eq!(
            worker_complete.security().worker_nonce(),
            FinalizerArtifactDescriptorConstraint::StagingOrSealed
        );
        worker_complete
            .security()
            .require_observed_pair(
                FinalizerArtifactSecurityPhase::Staging,
                FinalizerArtifactSecurityPhase::Staging,
            )
            .unwrap();

        store
            .persist_seal_progress(seal_progress_evidence(
                FinalizerSealArtifact::CandidateConsumption,
            ))
            .unwrap();
        let both_complete = store.recover().unwrap().unwrap();
        assert_eq!(
            both_complete.directive(),
            FinalizerCommitRecoveryDirective::RecoverGenerationSealTerminalAuthorization
        );
        assert_eq!(
            both_complete.security().phase(),
            FinalizerArtifactSecurityPhase::SealInProgress
        );
        both_complete
            .security()
            .require_observed_pair(
                FinalizerArtifactSecurityPhase::Staging,
                FinalizerArtifactSecurityPhase::Sealed,
            )
            .unwrap();
        assert_stage_unpublished(&root.0, FinalizerCommitStage::SealComplete);

        // Only the exact ten-object generation terminal authorization carried
        // by `persist` may cross the 03 -> 04 boundary. Legacy diagnostics are
        // deliberately invisible to this publication decision.
        persist(&store, &cases[4].0, cases[4].1.as_ref()).unwrap();
        assert_eq!(
            store.recover().unwrap().unwrap().directive(),
            FinalizerCommitRecoveryDirective::CompleteActiveHeadRuntimeAndPersistFinalCommit
        );
    }

    #[test]
    fn publishing_crashes_repair_only_private_residue_and_never_reuse_final_names() {
        let final_name = stage_file_name(FinalizerCommitStage::TransactionStarted);
        let private_name = publishing_name(final_name);

        let partial_root = TempRoot::new("partial-publishing");
        let partial_binding = binding_for_root(&partial_root.0);
        let partial_cases = states_and_writes(partial_binding);
        let partial_started = &partial_cases[0].0;
        fs::write(partial_root.0.join(&private_name), b"{").unwrap();
        let partial_store =
            FinalizerCommitReceiptStore::open_unsecured_test(&partial_root.0, partial_binding)
                .unwrap();
        assert_eq!(
            partial_store
                .persist_transaction_started(partial_started)
                .unwrap()
                .disposition(),
            ProtocolWriteDisposition::Created
        );
        assert!(!partial_root.0.join(&private_name).exists());

        let complete_root = TempRoot::new("complete-publishing");
        let complete_binding = binding_for_root(&complete_root.0);
        let complete_cases = states_and_writes(complete_binding);
        let complete_started = &complete_cases[0].0;
        let complete_store =
            FinalizerCommitReceiptStore::open_unsecured_test(&complete_root.0, complete_binding)
                .unwrap();
        let complete_bytes = DurableFinalizerCommitEnvelope::new(
            complete_started,
            complete_store.authenticated_root_sha256,
            None,
        )
        .unwrap()
        .canonical_json()
        .unwrap();
        fs::write(complete_root.0.join(&private_name), &complete_bytes).unwrap();
        assert_eq!(
            complete_store
                .persist_transaction_started(complete_started)
                .unwrap()
                .disposition(),
            ProtocolWriteDisposition::AlreadyIdentical
        );
        assert_eq!(
            fs::read(complete_root.0.join(final_name)).unwrap(),
            complete_bytes
        );
        assert!(!complete_root.0.join(&private_name).exists());

        let renamed_root = TempRoot::new("renamed-before-parent-flush");
        let renamed_binding = binding_for_root(&renamed_root.0);
        let renamed_cases = states_and_writes(renamed_binding);
        let renamed_started = &renamed_cases[0].0;
        let renamed_store =
            FinalizerCommitReceiptStore::open_unsecured_test(&renamed_root.0, renamed_binding)
                .unwrap();
        let renamed_bytes = DurableFinalizerCommitEnvelope::new(
            renamed_started,
            renamed_store.authenticated_root_sha256,
            None,
        )
        .unwrap()
        .canonical_json()
        .unwrap();
        fs::write(renamed_root.0.join(final_name), &renamed_bytes).unwrap();
        fs::write(renamed_root.0.join(&private_name), b"").unwrap();
        assert_eq!(
            renamed_store
                .persist_transaction_started(renamed_started)
                .unwrap()
                .disposition(),
            ProtocolWriteDisposition::AlreadyIdentical
        );
        assert!(!renamed_root.0.join(&private_name).exists());

        let poisoned_root = TempRoot::new("poisoned-final");
        let poisoned_binding = binding_for_root(&poisoned_root.0);
        let poisoned_cases = states_and_writes(poisoned_binding);
        let poisoned_started = &poisoned_cases[0].0;
        fs::write(poisoned_root.0.join(final_name), b"{").unwrap();
        let poisoned_store =
            FinalizerCommitReceiptStore::open_unsecured_test(&poisoned_root.0, poisoned_binding)
                .unwrap();
        assert_eq!(
            poisoned_store
                .persist_transaction_started(poisoned_started)
                .unwrap_err()
                .code(),
            "authority_finalizer_commit_store_receipt_invalid"
        );
        assert_eq!(fs::read(poisoned_root.0.join(final_name)).unwrap(), b"{");
    }

    #[test]
    fn durable_root_identity_is_access_independent_while_capabilities_remain_exact() {
        let identity = NativeFileIdentity {
            volume_serial: 41,
            file_id: [42; 16],
            byte_length: 0,
            link_count: 1,
            attributes: FILE_ATTRIBUTE_DIRECTORY,
        };
        let privileged = AuthenticatedFinalizerCommitRootReadback {
            identity,
            canonical_path_readback_sha256: [43; 32],
            complete_security_readback_sha256: [44; 32],
            granted_access_readback_sha256: [45; 32],
        };
        let restricted = AuthenticatedFinalizerCommitRootReadback {
            granted_access_readback_sha256: [46; 32],
            ..privileged
        };
        assert_eq!(
            authenticated_root_digest(&privileged),
            authenticated_root_digest(&restricted)
        );
        assert!(!same_authenticated_root_readback(&privileged, &restricted));
    }

    #[test]
    fn published_runtime_binding_projection_is_complete_and_phase_types_remain_distinct() {
        let root = TempRoot::new("published-runtime-binding");
        let binding = binding_for_root(&root.0);
        let plan = binding.plan_binding();
        let projection = VerifiedPublishedRuntimeBindingProjection::from_binding(binding).unwrap();
        assert_eq!(projection.capsule_sha256(), binding.capsule_sha256());
        assert_eq!(projection.plan_sha256(), binding.plan_sha256());
        assert_eq!(projection.generation_sha256(), binding.generation_sha256());
        assert_eq!(
            projection.transaction_sha256(),
            binding.transaction_sha256()
        );
        assert_eq!(
            projection.final_commit_store_root_identity_sha256(),
            binding.final_commit_store_root_identity_sha256()
        );
        assert_eq!(projection.operation(), plan.operation());
        assert_eq!(
            projection.expected_worker_service_identity_sha256(),
            plan.expected_worker_service_identity_sha256()
        );
        assert_eq!(
            projection.expected_worker_image_sha256(),
            plan.expected_worker_image_sha256()
        );
        assert_eq!(
            projection.exact_service_configuration_sha256(),
            plan.exact_service_configuration_sha256()
        );
        assert_eq!(
            projection.expected_service_image_sha256(),
            plan.expected_service_image_sha256()
        );
        assert_eq!(
            projection.expected_active_head_prior_sha256(),
            plan.expected_active_head_prior_sha256()
        );
        assert_eq!(
            projection.expected_active_head_replacement_sha256(),
            plan.expected_active_head_replacement_sha256()
        );
        assert_eq!(
            projection.expected_activation_manifest_sha256(),
            plan.expected_activation_manifest_sha256()
        );
        assert_eq!(
            projection.expected_activation_epoch(),
            plan.expected_activation_epoch()
        );
        assert_eq!(
            projection.generation_object_manifest_sha256(),
            plan.generation_object_manifest_sha256()
        );
        assert_eq!(
            projection.expected_runner_policy_state_byte_length(),
            plan.expected_runner_policy_state_byte_length()
        );
        assert_eq!(
            projection.expected_runner_policy_state_bytes_sha256(),
            plan.expected_runner_policy_state_bytes_sha256()
        );
        assert_eq!(
            projection.expected_runner_policy_state_binding_sha256(),
            plan.expected_runner_policy_state_binding_sha256()
        );
        let sealed_identity = RunnerPolicySealedIdentity::exact_test_fixture(0x6b);
        assert_eq!(
            projection.runner_policy_sealed_volume_serial(),
            sealed_identity.volume_serial()
        );
        assert_eq!(
            projection.runner_policy_sealed_file_id(),
            sealed_identity.file_id()
        );
        assert_eq!(
            projection.runner_policy_sealed_link_count(),
            sealed_identity.link_count()
        );
        assert_eq!(
            projection.runner_policy_sealed_attributes(),
            sealed_identity.attributes()
        );
        assert_eq!(
            projection.residue_plan_sha256(),
            plan.residue_plan_sha256().unwrap()
        );
        assert_eq!(
            projection.final_commit_gate_projection_sha256(),
            binding.final_commit_gate_projection_sha256()
        );
        assert_eq!(
            projection.expected_final_commit_gate_sha256(),
            binding.expected_final_commit_gate_sha256()
        );
        assert_eq!(
            VerifiedPublishedRuntimeBindingProjection::COMPLETE_FIELD_COUNT,
            41
        );
        for field in 0..VerifiedPublishedRuntimeBindingProjection::COMPLETE_FIELD_COUNT {
            assert_ne!(
                projection.complete_binding_sha256(),
                projection
                    .with_complete_field_drift_for_test(field)
                    .complete_binding_sha256(),
                "published projection field {field}"
            );
        }
        assert_ne!(
            std::any::TypeId::of::<VerifiedPublishedRuntimeLedgerPair>(),
            std::any::TypeId::of::<VerifiedPrecommitRuntimeLedgerPair>()
        );
        assert_ne!(
            std::any::TypeId::of::<VerifiedPublishedRuntimeLedgerNamespaceLease>(),
            std::any::TypeId::of::<VerifiedPrecommitRuntimeLedgerNamespaceLease>()
        );
        assert!(std::mem::needs_drop::<VerifiedPublishedRuntimeLedgerPair>());
        assert!(std::mem::needs_drop::<VerifiedPrecommitRuntimeLedgerPair>());
    }

    #[test]
    fn legacy_finalizer_commit_store_schema_is_rejected() {
        let root = TempRoot::new("legacy-finalizer-store-schema");
        let binding = binding_for_root(&root.0);
        let mut states = states_and_writes(binding);
        let state = states.remove(0).0;
        let authenticated_root_sha256 = binding.final_commit_store_root_identity_sha256();
        let mut envelope =
            DurableFinalizerCommitEnvelope::new(&state, authenticated_root_sha256, None).unwrap();
        envelope.schema = "vrcforge.authority.finalizer-commit-store.v2".to_string();
        assert_eq!(
            envelope
                .validate(
                    binding,
                    authenticated_root_sha256,
                    FinalizerCommitStage::TransactionStarted,
                    None,
                )
                .unwrap_err()
                .code(),
            "authority_finalizer_commit_store_binding_mismatch"
        );
    }

    #[test]
    fn restricted_reader_access_descriptor_and_drive_contracts_fail_closed() {
        let stable = canonical_security_sha256(STABLE_ROOT_SDDL).unwrap();
        let transaction =
            canonical_security_sha256(FINALIZER_COMMIT_TRANSACTION_ROOT_SDDL).unwrap();
        let immutable = canonical_security_sha256(FINALIZER_COMMIT_RECEIPT_IMMUTABLE_SDDL).unwrap();
        let sealed_generation = canonical_security_sha256(GENERATION_SEALED_SDDL).unwrap();
        let ledger_final = canonical_security_sha256(LEDGER_FINAL_SDDL).unwrap();
        validate_restricted_root_capability_observation(
            RestrictedFinalizerCommitRootHandleKind::StateRoot,
            RESTRICTED_PARENT_ROOT_ACCESS,
            stable,
        )
        .unwrap();
        validate_restricted_root_capability_observation(
            RestrictedFinalizerCommitRootHandleKind::TransactionRoot,
            RESTRICTED_TRANSACTION_ROOT_ACCESS,
            transaction,
        )
        .unwrap();
        validate_restricted_receipt_capability_observation(
            RESTRICTED_RECEIPT_READ_ACCESS,
            immutable,
        )
        .unwrap();
        validate_restricted_root_capability_observation(
            RestrictedFinalizerCommitRootHandleKind::GenerationNamespace,
            RESTRICTED_RUNTIME_GENERATION_ROOT_ACCESS,
            stable,
        )
        .unwrap();
        validate_restricted_root_capability_observation(
            RestrictedFinalizerCommitRootHandleKind::SealedGeneration,
            RESTRICTED_RUNTIME_GENERATION_ROOT_ACCESS,
            sealed_generation,
        )
        .unwrap();
        validate_restricted_runtime_ledger_capability_observation(
            RESTRICTED_RUNTIME_LEDGER_ACCESS,
            ledger_final,
        )
        .unwrap();

        for access in [
            RESTRICTED_TRANSACTION_ROOT_ACCESS & !FILE_LIST_DIRECTORY,
            RESTRICTED_TRANSACTION_ROOT_ACCESS | FILE_ADD_FILE,
            RESTRICTED_TRANSACTION_ROOT_ACCESS | ACCESS_SYSTEM_SECURITY,
        ] {
            assert_eq!(
                validate_restricted_root_capability_observation(
                    RestrictedFinalizerCommitRootHandleKind::TransactionRoot,
                    access,
                    transaction,
                )
                .unwrap_err()
                .code(),
                "authority_finalizer_commit_restricted_root_access_invalid"
            );
        }
        for access in [
            RESTRICTED_RECEIPT_READ_ACCESS & !FILE_READ_DATA,
            RESTRICTED_RECEIPT_READ_ACCESS | FILE_WRITE_DATA,
            RESTRICTED_RECEIPT_READ_ACCESS | DELETE,
            RESTRICTED_RECEIPT_READ_ACCESS | ACCESS_SYSTEM_SECURITY,
        ] {
            assert_eq!(
                validate_restricted_receipt_capability_observation(access, immutable)
                    .unwrap_err()
                    .code(),
                "authority_finalizer_commit_restricted_receipt_access_invalid"
            );
        }
        for descriptor in [
            canonical_security_sha256(FINALIZER_PUBLISHING_PRIVATE_SEALED_SDDL).unwrap(),
            canonical_security_sha256(STATE_STAGING_SDDL).unwrap(),
            [47; 32],
        ] {
            assert_eq!(
                validate_restricted_receipt_capability_observation(
                    RESTRICTED_RECEIPT_READ_ACCESS,
                    descriptor,
                )
                .unwrap_err()
                .code(),
                "authority_finalizer_commit_restricted_receipt_security_invalid"
            );
        }
        for access in [
            RESTRICTED_RUNTIME_LEDGER_ACCESS & !FILE_WRITE_DATA,
            RESTRICTED_RUNTIME_LEDGER_ACCESS | DELETE,
            RESTRICTED_RUNTIME_LEDGER_ACCESS | WRITE_DAC,
            RESTRICTED_RUNTIME_LEDGER_ACCESS | WRITE_OWNER_ACCESS,
            RESTRICTED_RUNTIME_LEDGER_ACCESS | ACCESS_SYSTEM_SECURITY,
        ] {
            assert_eq!(
                validate_restricted_runtime_ledger_capability_observation(access, ledger_final)
                    .unwrap_err()
                    .code(),
                "authority_finalizer_commit_runtime_ledger_access_invalid"
            );
        }
        for descriptor in [stable, sealed_generation, immutable, [49; 32]] {
            assert_eq!(
                validate_restricted_runtime_ledger_capability_observation(
                    RESTRICTED_RUNTIME_LEDGER_ACCESS,
                    descriptor,
                )
                .unwrap_err()
                .code(),
                "authority_finalizer_commit_runtime_ledger_security_invalid"
            );
        }

        for drive_type in [0, 1, 2, 4, 5, 6, u32::MAX] {
            assert!(!drive_type_is_fixed(drive_type));
        }
        assert!(drive_type_is_fixed(DRIVE_FIXED));
        assert!(access_requires_security_privilege(
            AUTHENTICATED_TRANSACTION_ROOT_ACCESS
        ));
        assert!(access_requires_security_privilege(
            AUTHENTICATED_RECEIPT_READ_ACCESS
        ));
        assert!(!access_requires_security_privilege(
            RESTRICTED_TRANSACTION_ROOT_ACCESS
        ));
        assert!(!access_requires_security_privilege(
            RESTRICTED_RECEIPT_READ_ACCESS
        ));
        assert!(FINALIZER_COMMIT_TRANSACTION_ROOT_SDDL.contains(
            "(A;;0x001200a1;;;S-1-5-80-627086344-872206109-3199044541-2745001037-75066892)"
        ));
        assert!(FINALIZER_COMMIT_TRANSACTION_ROOT_SDDL.contains(
            "(A;;0x001200a0;;;S-1-5-80-1152445285-3302248683-2168573404-3713171798-555061439)"
        ));
        assert!(LEDGER_FINAL_SDDL.contains(
            "(A;;0x0012008f;;;S-1-5-80-627086344-872206109-3199044541-2745001037-75066892)"
        ));
        assert!(!LEDGER_FINAL_SDDL
            .contains("S-1-5-80-1152445285-3302248683-2168573404-3713171798-555061439"));
        assert_eq!(RESTRICTED_RUNTIME_LEDGER_SHARE_ACCESS, 0);
    }

    #[test]
    fn runtime_ledger_files_are_opened_share_denying_until_the_pair_is_dropped() {
        let root = TempRoot::new("exclusive-ledger-pair");
        fs::write(root.0.join("ledger.bin"), b"ledger").unwrap();
        let parent = open_root_for_test(&root.0).unwrap();
        let held = nt_open_relative_with_share(
            &parent,
            "ledger.bin",
            FILE_OPEN,
            RECEIPT_READ_ACCESS,
            None,
            RESTRICTED_RUNTIME_LEDGER_SHARE_ACCESS,
        )
        .unwrap()
        .unwrap();
        assert_eq!(
            open_relative_optional(&parent, "ledger.bin", RECEIPT_READ_ACCESS)
                .unwrap_err()
                .code(),
            "authority_finalizer_commit_store_receipt_open_failed"
        );
        drop(held);
        assert!(
            open_relative_optional(&parent, "ledger.bin", RECEIPT_READ_ACCESS)
                .unwrap()
                .is_some()
        );
    }

    #[test]
    fn privileged_writer_and_restricted_reader_share_one_exact_final_chain() {
        let root = TempRoot::new("cross-lane-final");
        let binding = binding_for_root(&root.0);
        let cases = states_and_writes(binding);
        let store = FinalizerCommitReceiptStore::open_unsecured_test(&root.0, binding).unwrap();
        for (state, write) in &cases {
            persist(&store, state, write.as_ref()).unwrap();
        }
        let expected_projection = cases
            .last()
            .unwrap()
            .0
            .final_commit_persistence_projection()
            .unwrap()
            .unwrap();
        drop(store);

        let reader =
            RestrictedFinalizerCommitTransactionRoot::open_unsecured_test(&root.0).unwrap();
        let chain = reader
            .strict_scan_exact(
                binding.transaction_sha256(),
                FinalizerCommitStage::FinalCommit,
            )
            .unwrap();
        let readback = recovered_final_commit_readback(
            &chain,
            binding.transaction_sha256(),
            reader.authenticated_root_sha256(),
        )
        .unwrap();
        assert_eq!(chain.stages.len(), STAGES.len());
        assert_eq!(readback.projection(), expected_projection);
        assert_eq!(
            readback.projection().zero_residue().digest().unwrap(),
            expected_projection.zero_residue().digest().unwrap()
        );
        chain.revalidate_held(&reader).unwrap();
    }

    #[test]
    fn restricted_precommit_reader_accepts_only_exact_seal_complete_shape() {
        let root = TempRoot::new("cross-lane-precommit");
        let binding = binding_for_root(&root.0);
        let cases = states_and_writes(binding);
        let store = FinalizerCommitReceiptStore::open_unsecured_test(&root.0, binding).unwrap();
        for (state, write) in &cases[..5] {
            persist(&store, state, write.as_ref()).unwrap();
        }
        drop(store);

        let reader =
            RestrictedFinalizerCommitTransactionRoot::open_unsecured_test(&root.0).unwrap();
        let chain = reader
            .strict_scan_exact(
                binding.transaction_sha256(),
                FinalizerCommitStage::SealComplete,
            )
            .unwrap();
        let readback = recovered_seal_complete_readback(
            &chain,
            binding.transaction_sha256(),
            reader.authenticated_root_sha256(),
        )
        .unwrap();
        assert_eq!(readback.binding, binding);
        assert_eq!(readback.tip.stage(), FinalizerCommitStage::SealComplete);
        assert_eq!(chain.stages.len(), 5);
        chain.revalidate_held(&reader).unwrap();
        drop(chain);
        drop(reader);

        let store = FinalizerCommitReceiptStore::open_unsecured_test(&root.0, binding).unwrap();
        persist(&store, &cases[5].0, cases[5].1.as_ref()).unwrap();
        drop(store);
        let reader =
            RestrictedFinalizerCommitTransactionRoot::open_unsecured_test(&root.0).unwrap();
        assert_eq!(
            reader
                .strict_scan_exact(
                    binding.transaction_sha256(),
                    FinalizerCommitStage::SealComplete,
                )
                .unwrap_err()
                .code(),
            "authority_finalizer_commit_restricted_chain_shape_invalid"
        );

        let partial = TempRoot::new("cross-lane-precommit-partial");
        let partial_binding = binding_for_root(&partial.0);
        let partial_cases = states_and_writes(partial_binding);
        let partial_store =
            FinalizerCommitReceiptStore::open_unsecured_test(&partial.0, partial_binding).unwrap();
        for (state, write) in &partial_cases[..4] {
            persist(&partial_store, state, write.as_ref()).unwrap();
        }
        drop(partial_store);
        let partial_reader =
            RestrictedFinalizerCommitTransactionRoot::open_unsecured_test(&partial.0).unwrap();
        assert_eq!(
            partial_reader
                .strict_scan_exact(
                    partial_binding.transaction_sha256(),
                    FinalizerCommitStage::SealComplete,
                )
                .unwrap_err()
                .code(),
            "authority_finalizer_commit_restricted_chain_incomplete"
        );
    }

    #[test]
    fn restricted_reader_rejects_private_and_unknown_namespace_members() {
        for (label, hostile_name, expected) in [
            (
                "restricted-private",
                publishing_name(stage_file_name(FinalizerCommitStage::ApplyReady)),
                "authority_finalizer_commit_restricted_namespace_residue",
            ),
            (
                "restricted-unknown",
                "unknown.receipt.json".to_string(),
                "authority_finalizer_commit_transaction_namespace_unknown",
            ),
        ] {
            let root = TempRoot::new(label);
            let binding = binding_for_root(&root.0);
            let cases = states_and_writes(binding);
            let store = FinalizerCommitReceiptStore::open_unsecured_test(&root.0, binding).unwrap();
            for (state, write) in &cases {
                persist(&store, state, write.as_ref()).unwrap();
            }
            drop(store);
            fs::write(root.0.join(hostile_name), b"hostile").unwrap();
            let reader =
                RestrictedFinalizerCommitTransactionRoot::open_unsecured_test(&root.0).unwrap();
            assert_eq!(
                reader
                    .strict_scan_exact(
                        binding.transaction_sha256(),
                        FinalizerCommitStage::FinalCommit,
                    )
                    .unwrap_err()
                    .code(),
                expected
            );
        }
    }

    #[test]
    fn published_runtime_adoption_stays_opaque_until_first_replay() {
        fn struct_body<'a>(source: &'a str, declaration: &str) -> &'a str {
            let tail = source
                .split_once(declaration)
                .map(|(_, tail)| tail)
                .expect("adoption struct declaration must remain present");
            let end = tail
                .find("\n}\n\nimpl")
                .or_else(|| tail.find("\r\n}\r\n\r\nimpl"))
                .expect("adoption struct must remain directly followed by its impl");
            &tail[..end]
        }

        let store_source = include_str!("finalizer_commit_store_windows.rs");
        let ledger_source = include_str!("../primitive_evidence_authority_ledger.rs");
        let blob_source = include_str!("../primitive_evidence_authority_blob.rs");
        for declaration in [
            "pub(crate) struct VerifiedPublishedRuntimeAdoption {",
            "pub(crate) struct VerifiedPublishedProtectedBlobNamespaceAdoption {",
        ] {
            let body = struct_body(store_source, declaration);
            assert!(!body.contains("pub("));
        }
        assert!(!ledger_source.contains("let VerifiedPublishedRuntimeAdoption {"));
        assert!(!blob_source.contains("let VerifiedPublishedProtectedBlobNamespaceAdoption {"));
        assert_eq!(ledger_source.matches("adoption.consume_with(").count(), 1);
        assert_eq!(blob_source.matches("adoption.consume_with(").count(), 1);

        let consume = ledger_source
            .find("adoption.consume_with(")
            .expect("ledger must consume the opaque adoption exactly once");
        let authenticate_blob = ledger_source[consume..]
            .find("AuthenticatedProtectedBlobNamespace::from_verified_final_commit(")
            .map(|offset| consume + offset)
            .expect("blob namespace must be authenticated inside adoption consumption");
        let first_replay = ledger_source[authenticate_blob..]
            .find("Self::load_opened_pair(")
            .map(|offset| authenticate_blob + offset)
            .expect("first replay must occur inside adoption consumption");
        assert!(consume < authenticate_blob && authenticate_blob < first_replay);
    }
}

use super::{
    bootstrap_activation::{
        candidate_credential_file_name, CandidateCredentialPhase, CandidateCredentialRecord,
        CandidateProcessEvidence, MAX_CANDIDATE_CREDENTIAL_BYTES,
    },
    receipt::{source_full_readback_receipt, VerifiedMaintenanceLease},
    worker::{
        authorize_candidate_credential_armed, authorize_capsule_staged,
        authorize_claimed_worker_started, authorize_finalizer_handles_closed,
        authorize_partial_staging_contained, authorize_pipe_prepared, authorize_pipe_recovered,
        authorize_service_absent_after_handles_closed, authorize_service_created_after_pipe,
        authorize_service_delete_intent_after_exit_ready,
        authorize_service_delete_pending_after_intent, authorize_source_handles_bound_after_intent,
        authorize_source_stage_resolved, authorize_source_staging_intent,
        authorize_transaction_committed, authorize_transaction_committed_after_candidate_armed,
        authorize_transaction_contained, authorize_transaction_contained_after_candidate_armed,
        authorize_transaction_start, authorize_worker_exit_ready,
        authorize_worker_invocation_claimed, authorize_worker_started, encode_worker_journal,
        encode_worker_journal_append, parse_worker_journal_recovery,
        worker_bootstrap_file_readback_receipt, CandidateCredentialArmedReceipt,
        DurableSourceStagingReceipt, DurableStagedPayloadBinding, FinalizerHandlesClosedReceipt,
        MaintenanceWorkerCapsule, MaintenanceWorkerJournalRecord, MaintenanceWorkerLaunchContract,
        MaintenanceWorkerPhase, ServiceAbsentReceipt, ServiceCreatedReceipt,
        ServiceDeleteIntentReceipt, ServiceDeletePendingReceipt, StagedPayloadKind,
        TransactionCommittedReceipt, TransactionContainedReceipt, TransactionStartedReceipt,
        WorkerBootstrapIntentReceipt, WorkerBootstrapStagedFileBinding,
        WorkerBootstrapStagingReceipt, WorkerExitReadyReceipt, WorkerHandleHandoffReceipt,
        WorkerInvocationClaimReceipt, WorkerNonceConsumptionReceipt,
        WorkerPartialStagingCleanupReceipt, WorkerPipePreparedReceipt, WorkerPipeRecoveryReceipt,
        WorkerProcessBinding, WorkerSourceIdentityLedger, WorkerSourceStagingIntentReceipt,
        WorkerStagingCleanupReceipt, WorkerStartedReceipt,
    },
    AuthorityMaintenanceError, AuthorityMaintenanceOperation, AuthorityPayloadDigest,
    BINARY_DIRECTORY_SDDL, BINARY_FILE_SDDL, BINARY_GENERATION_DIRECTORY_SDDL,
    CANDIDATE_ACTIVATION_DIRECTORY_SDDL, CANDIDATE_CONSUMPTION_DIRECTORY_SDDL,
    CANDIDATE_CONSUMPTION_FILE_SDDL, PROTECTED_GENERATION_PAYLOAD_COUNT, STATE_DIRECTORY_SDDL,
    STATE_FILE_SDDL, STATE_GENERATION_DIRECTORY_SDDL, WORKER_NONCE_DIRECTORY_SDDL,
    WORKER_NONCE_FILE_SDDL,
};
#[cfg(test)]
use super::{
    KEY_SECURITY_SDDL, MAINTENANCE_SERVICE_SID, SEALED_NONCE_FILE_SDDL, SERVICE_SECURITY_SDDL,
};
use crate::primitive_evidence_authority_windows::AuthorityLayout;
use sha2::{Digest, Sha256};
use std::{
    mem::{size_of, zeroed},
    os::windows::{
        ffi::{OsStrExt, OsStringExt},
        io::{AsRawHandle, FromRawHandle, OwnedHandle, RawHandle},
    },
    path::Path,
    ptr,
    time::{SystemTime, UNIX_EPOCH},
};
use windows_sys::{
    Wdk::{
        Foundation::OBJECT_ATTRIBUTES,
        Storage::FileSystem::{
            FileDispositionInformationEx, FileRenameInformation, NtCreateFile, NtFlushBuffersFile,
            NtSetInformationFile, FILE_CREATE, FILE_DIRECTORY_FILE, FILE_DISPOSITION_DELETE,
            FILE_DISPOSITION_INFORMATION_EX, FILE_NON_DIRECTORY_FILE, FILE_OPEN, FILE_OPEN_IF,
            FILE_OPEN_REPARSE_POINT, FILE_RENAME_INFORMATION, FILE_SYNCHRONOUS_IO_NONALERT,
            FILE_WRITE_THROUGH,
        },
    },
    Win32::{
        Foundation::{
            GetLastError, LocalFree, SetLastError, ERROR_FILE_NOT_FOUND, ERROR_NOT_ALL_ASSIGNED,
            ERROR_PATH_NOT_FOUND, HANDLE, INVALID_HANDLE_VALUE, LUID, UNICODE_STRING,
        },
        Security::{
            AdjustTokenPrivileges,
            Authorization::{
                ConvertSecurityDescriptorToStringSecurityDescriptorW,
                ConvertStringSecurityDescriptorToSecurityDescriptorW, GetSecurityInfo,
                SDDL_REVISION_1, SE_FILE_OBJECT,
            },
            LookupPrivilegeValueW, DACL_SECURITY_INFORMATION, GROUP_SECURITY_INFORMATION,
            LABEL_SECURITY_INFORMATION, LUID_AND_ATTRIBUTES, OWNER_SECURITY_INFORMATION,
            PSECURITY_DESCRIPTOR, SE_PRIVILEGE_ENABLED, TOKEN_ADJUST_PRIVILEGES, TOKEN_PRIVILEGES,
            TOKEN_QUERY,
        },
        Storage::FileSystem::{
            CreateFileW, GetFileInformationByHandle, GetFinalPathNameByHandleW, ReadFile,
            SetFilePointerEx, WriteFile, BY_HANDLE_FILE_INFORMATION, DELETE, FILE_ADD_FILE,
            FILE_ADD_SUBDIRECTORY, FILE_APPEND_DATA, FILE_ATTRIBUTE_DIRECTORY,
            FILE_ATTRIBUTE_NORMAL, FILE_ATTRIBUTE_REPARSE_POINT, FILE_BEGIN,
            FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT, FILE_LIST_DIRECTORY,
            FILE_READ_ATTRIBUTES, FILE_READ_DATA, FILE_READ_EA, FILE_SHARE_DELETE, FILE_SHARE_READ,
            FILE_SHARE_WRITE, FILE_TRAVERSE, FILE_WRITE_DATA, OPEN_EXISTING, READ_CONTROL,
            SYNCHRONIZE,
        },
        System::{
            Kernel::OBJ_CASE_INSENSITIVE,
            SystemServices::ACCESS_SYSTEM_SECURITY,
            Threading::{GetCurrentProcess, OpenProcessToken},
            IO::IO_STATUS_BLOCK,
        },
    },
};

const FILE_OPENED_INFORMATION: usize = 1;
const FILE_CREATED_INFORMATION: usize = 2;
const STATUS_OBJECT_NAME_NOT_FOUND: i32 = 0xc000_0034u32 as i32;
const STATUS_OBJECT_NAME_COLLISION: i32 = 0xc000_0035u32 as i32;
const STATUS_OBJECT_PATH_NOT_FOUND: i32 = 0xc000_003au32 as i32;
const STATUS_NO_SUCH_FILE: i32 = 0xc000_000fu32 as i32;
const SECURITY_INFORMATION: u32 = OWNER_SECURITY_INFORMATION
    | GROUP_SECURITY_INFORMATION
    | DACL_SECURITY_INFORMATION
    | LABEL_SECURITY_INFORMATION;
// The long-lived authority service intentionally has no SeSecurityPrivilege.
// Candidate consumption therefore verifies the exact owner, group, and
// protected DACL without requesting SACL access. The pre-provisioned
// high-integrity parent supplies the inherited mandatory label; the elevated
// finalizer owns the later full SACL readback and sealed descriptor transition.
const CANDIDATE_SECURITY_INFORMATION: u32 =
    OWNER_SECURITY_INFORMATION | GROUP_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION;
const MAX_PROTECTED_RECEIPT_BYTES: usize = 64 * 1024;
const MAX_COPY_BUFFER_BYTES: usize = 64 * 1024;
const DIRECTORY_READ_ACCESS: u32 = FILE_LIST_DIRECTORY
    | FILE_TRAVERSE
    | FILE_READ_ATTRIBUTES
    | FILE_READ_EA
    | READ_CONTROL
    | SYNCHRONIZE;
const DIRECTORY_CREATE_CHILD_ACCESS: u32 =
    DIRECTORY_READ_ACCESS | FILE_ADD_FILE | FILE_ADD_SUBDIRECTORY;
const DIRECTORY_CREATE_FILE_ACCESS: u32 = DIRECTORY_READ_ACCESS | FILE_ADD_FILE;
const FILE_READ_ACCESS: u32 =
    FILE_READ_DATA | FILE_READ_ATTRIBUTES | FILE_READ_EA | READ_CONTROL | SYNCHRONIZE;
const FILE_WRITE_ONCE_ACCESS: u32 = FILE_READ_ACCESS | FILE_WRITE_DATA | FILE_APPEND_DATA;
const CANDIDATE_PUBLICATION_DIRECTORY_ACCESS: u32 = DIRECTORY_CREATE_FILE_ACCESS;
const CANDIDATE_PRIVATE_STAGING_FILE_ACCESS: u32 = FILE_WRITE_ONCE_ACCESS | DELETE;
const CANDIDATE_READONLY_DIRECTORY_ACCESS: u32 = DIRECTORY_READ_ACCESS;
const CANDIDATE_READONLY_FILE_ACCESS: u32 = FILE_READ_ACCESS;
const SECURITY_AUDIT_PRIVILEGE: &str = "SeSecurityPrivilege";
const WORKER_EXECUTABLE_NAME: &str = "vrcforge_primitive_evidence_install_helper.exe";
const CAPSULE_FILE_NAME: &str = "capsule.json";
const INTENT_FILE_NAME: &str = "bootstrap-intent.json";
const BOOTSTRAP_RECEIPT_FILE_NAME: &str = "bootstrap-receipt.json";
const JOURNAL_FILE_NAME: &str = "journal.jsonl";
const HANDOFF_RECEIPT_FILE_NAME: &str = "handle-handoff-receipt.json";
const PIPE_PREPARED_RECEIPT_FILE_NAME: &str = "pipe-prepared-receipt.json";
const PIPE_RECOVERY_RECEIPT_FILE_NAME: &str = "pipe-recovery-receipt.json";
const SERVICE_CREATED_RECEIPT_FILE_NAME: &str = "service-created-receipt.json";
const WORKER_INVOCATION_CLAIM_RECEIPT_FILE_NAME: &str = "worker-invocation-claim-receipt.json";
const WORKER_STARTED_RECEIPT_FILE_NAME: &str = "worker-started-receipt.json";
const SOURCE_IDENTITY_LEDGER_FILE_NAME: &str = "source-identities.json";
const SOURCE_STAGING_INTENT_RECEIPT_FILE_NAME: &str = "source-staging-intent-receipt.json";
const PARTIAL_STAGING_CLEANUP_RECEIPT_FILE_NAME: &str = "partial-staging-cleanup-receipt.json";
const SOURCE_STAGING_RECEIPT_FILE_NAME: &str = "source-staging-receipt.json";
const TRANSACTION_STARTED_RECEIPT_FILE_NAME: &str = "transaction-started-receipt.json";
const CANDIDATE_CREDENTIAL_ARMED_RECEIPT_FILE_NAME: &str =
    "candidate-credential-armed-receipt.json";
const CANDIDATE_PREPARED_SUFFIX: &str = ".prepared";
const CANDIDATE_PRIVATE_STAGING_SUFFIX: &str = ".publishing";
const TRANSACTION_COMMITTED_RECEIPT_FILE_NAME: &str = "transaction-committed-receipt.json";
const TRANSACTION_CONTAINED_RECEIPT_FILE_NAME: &str = "transaction-contained-receipt.json";
const WORKER_EXIT_READY_RECEIPT_FILE_NAME: &str = "exit-ready-receipt.json";
const SERVICE_DELETE_INTENT_RECEIPT_FILE_NAME: &str = "service-delete-intent-receipt.json";
const SERVICE_DELETE_PENDING_RECEIPT_FILE_NAME: &str = "service-delete-pending-receipt.json";
const FINALIZER_HANDLES_CLOSED_RECEIPT_FILE_NAME: &str = "finalizer-handles-closed-receipt.json";
const STAGING_CLEANUP_RECEIPT_FILE_NAME: &str = "staging-cleanup-receipt.json";
const SERVICE_ABSENT_RECEIPT_FILE_NAME: &str = "service-absent-receipt.json";
const STAGED_PAYLOAD_KINDS: [StagedPayloadKind; PROTECTED_GENERATION_PAYLOAD_COUNT] = [
    StagedPayloadKind::Service,
    StagedPayloadKind::Controller,
    StagedPayloadKind::InstallHelper,
    StagedPayloadKind::LifecycleDriver,
    StagedPayloadKind::BridgeLauncher,
    StagedPayloadKind::RuntimeSourceManifest,
];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum NativePartialStagingCleanupPhase {
    CloseSourceHandles,
    OpenStageDirectory,
    DeleteServicePayload,
    DeleteControllerPayload,
    DeleteInstallHelperPayload,
    DeleteLifecycleDriverPayload,
    DeleteBridgeLauncherPayload,
    DeleteRuntimeSourceManifestPayload,
    DeleteIdentityLedger,
    DeleteStageDirectory,
    VerifyStageAbsent,
    FlushStateParent,
}

pub(super) const NATIVE_PARTIAL_STAGING_CLEANUP_PHASES: [NativePartialStagingCleanupPhase; 12] = [
    NativePartialStagingCleanupPhase::CloseSourceHandles,
    NativePartialStagingCleanupPhase::OpenStageDirectory,
    NativePartialStagingCleanupPhase::DeleteServicePayload,
    NativePartialStagingCleanupPhase::DeleteControllerPayload,
    NativePartialStagingCleanupPhase::DeleteInstallHelperPayload,
    NativePartialStagingCleanupPhase::DeleteLifecycleDriverPayload,
    NativePartialStagingCleanupPhase::DeleteBridgeLauncherPayload,
    NativePartialStagingCleanupPhase::DeleteRuntimeSourceManifestPayload,
    NativePartialStagingCleanupPhase::DeleteIdentityLedger,
    NativePartialStagingCleanupPhase::DeleteStageDirectory,
    NativePartialStagingCleanupPhase::VerifyStageAbsent,
    NativePartialStagingCleanupPhase::FlushStateParent,
];

pub(super) fn run_partial_staging_cleanup_steps<F>(
    mut apply: F,
) -> Result<(), AuthorityMaintenanceError>
where
    F: FnMut(NativePartialStagingCleanupPhase) -> Result<(), AuthorityMaintenanceError>,
{
    for phase in NATIVE_PARTIAL_STAGING_CLEANUP_PHASES {
        apply(phase)?;
    }
    Ok(())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum NativeCandidateTombstonePersistencePhase {
    WriteExact,
    FlushFile,
    FlushParent,
    VerifySecurityPathIdentityAndBytes,
}

const NATIVE_CANDIDATE_TOMBSTONE_PERSISTENCE_PHASES: [NativeCandidateTombstonePersistencePhase; 4] = [
    NativeCandidateTombstonePersistencePhase::WriteExact,
    NativeCandidateTombstonePersistencePhase::FlushFile,
    NativeCandidateTombstonePersistencePhase::FlushParent,
    NativeCandidateTombstonePersistencePhase::VerifySecurityPathIdentityAndBytes,
];

fn run_candidate_tombstone_persistence<F>(mut apply: F) -> Result<(), AuthorityMaintenanceError>
where
    F: FnMut(NativeCandidateTombstonePersistencePhase) -> Result<(), AuthorityMaintenanceError>,
{
    for phase in NATIVE_CANDIDATE_TOMBSTONE_PERSISTENCE_PHASES {
        apply(phase)?;
    }
    Ok(())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
enum NativeCandidateAtomicPublicationPhase {
    CreatePrivateStaging,
    WriteExact,
    FlushStaging,
    VerifyPrivateStaging,
    RenameNoReplace,
    FlushParent,
    CloseWritableHandles,
    ReopenReadOnly,
    VerifyPublished,
}

const NATIVE_CANDIDATE_ATOMIC_PUBLICATION_PHASES: [NativeCandidateAtomicPublicationPhase; 9] = [
    NativeCandidateAtomicPublicationPhase::CreatePrivateStaging,
    NativeCandidateAtomicPublicationPhase::WriteExact,
    NativeCandidateAtomicPublicationPhase::FlushStaging,
    NativeCandidateAtomicPublicationPhase::VerifyPrivateStaging,
    NativeCandidateAtomicPublicationPhase::RenameNoReplace,
    NativeCandidateAtomicPublicationPhase::FlushParent,
    NativeCandidateAtomicPublicationPhase::CloseWritableHandles,
    NativeCandidateAtomicPublicationPhase::ReopenReadOnly,
    NativeCandidateAtomicPublicationPhase::VerifyPublished,
];

fn run_candidate_atomic_publication<F>(mut apply: F) -> Result<(), AuthorityMaintenanceError>
where
    F: FnMut(NativeCandidateAtomicPublicationPhase) -> Result<(), AuthorityMaintenanceError>,
{
    for phase in NATIVE_CANDIDATE_ATOMIC_PUBLICATION_PHASES {
        apply(phase)?;
    }
    Ok(())
}

pub(super) struct NativeWorkerSourceStore {
    _directory: OwnedHandle,
    _source_handles: Option<[OwnedHandle; PROTECTED_GENERATION_PAYLOAD_COUNT]>,
    _staged_files: [OwnedHandle; PROTECTED_GENERATION_PAYLOAD_COUNT],
    _identity_ledger_file: OwnedHandle,
    identity_ledger: WorkerSourceIdentityLedger,
}

pub(super) struct NativeNonceConsumptionLease {
    _state_chain: Vec<OwnedHandle>,
    _nonce_root: OwnedHandle,
    _receipt_file: OwnedHandle,
    receipt: WorkerNonceConsumptionReceipt,
    nonce_root_volume_serial: u64,
    nonce_root_file_id: [u8; 16],
    file_identity: NativeFileIdentity,
    bytes_sha256: [u8; 32],
}

pub(super) struct NativeCandidateConsumptionLease {
    _state_chain: Vec<OwnedHandle>,
    _nonce_root: OwnedHandle,
    _receipt_file: OwnedHandle,
    bytes: Vec<u8>,
    file_identity: NativeFileIdentity,
    bytes_sha256: [u8; 32],
}

pub(super) struct NativeCandidateCredentialLease {
    _directory: OwnedHandle,
    _file: OwnedHandle,
    directory_identity: NativeFileIdentity,
    record: CandidateCredentialRecord,
    file_identity: NativeFileIdentity,
    bytes_sha256: [u8; 32],
}

impl NativeCandidateCredentialLease {
    pub(super) fn record(&self) -> &CandidateCredentialRecord {
        &self.record
    }

    pub(super) fn durable_identity(&self) -> (u64, [u8; 16], [u8; 32]) {
        (
            self.file_identity.volume_serial,
            self.file_identity.file_id,
            self.bytes_sha256,
        )
    }

    pub(super) fn verify_prepared_readback(
        &self,
        layout: &AuthorityLayout,
    ) -> Result<(u64, [u8; 16], [u8; 32]), AuthorityMaintenanceError> {
        let binding = self
            .record
            .binding()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        let final_name = candidate_credential_file_name(binding.transaction_sha256())
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        let prepared_name = format!("{final_name}{CANDIDATE_PREPARED_SUFFIX}");
        verify_candidate_credential_lease(
            layout,
            &prepared_name,
            CandidateCredentialPhase::Prepared,
            self,
        )?;
        Ok(self.durable_identity())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct NativeCandidateActivationReceiptBinding {
    pub(super) capsule_sha256: [u8; 32],
    pub(super) plan_sha256: [u8; 32],
    pub(super) generation_sha256: [u8; 32],
    pub(super) transaction_sha256: [u8; 32],
    pub(super) transaction_started_receipt_sha256: [u8; 32],
    pub(super) worker_started_receipt_sha256: [u8; 32],
    pub(super) maintenance_worker: CandidateProcessEvidence,
    pub(super) candidate_credential_sha256: [u8; 32],
    pub(super) candidate_credential_armed_record_sha256: [u8; 32],
    pub(super) candidate_credential_armed_receipt_sha256: [u8; 32],
    pub(super) candidate_credential_armed_journal_record_sha256: [u8; 32],
    pub(super) candidate_credential_armed_journal_sequence: u64,
    pub(super) candidate_service: CandidateProcessEvidence,
    pub(super) nonce_consumption_receipt_sha256: [u8; 32],
    pub(super) nonce_consumption_full_readback_sha256: [u8; 32],
    pub(super) nonce_consumption_file_sha256: [u8; 32],
    pub(super) nonce_consumption_file_volume_serial: u64,
    pub(super) nonce_consumption_file_id: [u8; 16],
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct NativeRetirementTransactionReceiptBinding {
    pub(super) capsule_sha256: [u8; 32],
    pub(super) plan_sha256: [u8; 32],
    pub(super) generation_sha256: [u8; 32],
    pub(super) transaction_sha256: [u8; 32],
    pub(super) source_handles_bound_record_sha256: [u8; 32],
    pub(super) source_staging_receipt_sha256: [u8; 32],
    pub(super) transaction_started_receipt_sha256: [u8; 32],
    pub(super) transaction_started_journal_record_sha256: [u8; 32],
    pub(super) transaction_started_journal_sequence: u64,
    pub(super) worker_started_receipt_sha256: [u8; 32],
    pub(super) nonce_consumption_receipt_sha256: [u8; 32],
    pub(super) nonce_consumption_full_readback_sha256: [u8; 32],
    pub(super) nonce_consumption_file_sha256: [u8; 32],
    pub(super) nonce_consumption_file_volume_serial: u64,
    pub(super) nonce_consumption_file_id: [u8; 16],
}

pub(super) struct NativePersistedReceipt<T> {
    _receipt_file: OwnedHandle,
    receipt: T,
    file_identity: NativeFileIdentity,
    bytes_sha256: [u8; 32],
}

impl<T> NativePersistedReceipt<T> {
    pub(super) fn receipt(&self) -> &T {
        &self.receipt
    }

    pub(super) fn durable_identity(&self) -> (u64, [u8; 16], [u8; 32]) {
        (
            self.file_identity.volume_serial,
            self.file_identity.file_id,
            self.bytes_sha256,
        )
    }
}

pub(super) type NativePersistedPipePrepared = NativePersistedReceipt<WorkerPipePreparedReceipt>;

#[derive(Default)]
struct NativeWorkerRecoveryReceipts {
    original_pipe: Option<WorkerPipePreparedReceipt>,
    pipe: Option<WorkerPipePreparedReceipt>,
    pipe_recovery: Option<WorkerPipeRecoveryReceipt>,
    service_created: Option<ServiceCreatedReceipt>,
    handoff: Option<WorkerHandleHandoffReceipt>,
    invocation_claim: Option<WorkerInvocationClaimReceipt>,
    worker_started: Option<WorkerStartedReceipt>,
    staging_intent: Option<WorkerSourceStagingIntentReceipt>,
    partial_staging_cleanup: Option<WorkerPartialStagingCleanupReceipt>,
    staging: Option<DurableSourceStagingReceipt>,
    nonce_consumption: Option<WorkerNonceConsumptionReceipt>,
    transaction_started: Option<TransactionStartedReceipt>,
    candidate_credential_armed: Option<CandidateCredentialArmedReceipt>,
    transaction_committed: Option<TransactionCommittedReceipt>,
    transaction_contained: Option<TransactionContainedReceipt>,
    exit_ready: Option<WorkerExitReadyReceipt>,
    delete_intent: Option<ServiceDeleteIntentReceipt>,
    delete_pending: Option<ServiceDeletePendingReceipt>,
    handles_closed: Option<FinalizerHandlesClosedReceipt>,
    cleanup: Option<WorkerStagingCleanupReceipt>,
    service_absent: Option<ServiceAbsentReceipt>,
}

impl NativeNonceConsumptionLease {
    pub(super) fn receipt(&self) -> &WorkerNonceConsumptionReceipt {
        &self.receipt
    }

    fn durable_binding(
        &self,
        capsule: &MaintenanceWorkerCapsule,
    ) -> Result<([u8; 32], [u8; 32], [u8; 32], u64, [u8; 16]), AuthorityMaintenanceError> {
        self.receipt.validate(capsule)?;
        let nonce_root_identity = file_identity(&self._nonce_root)?;
        verify_protected_file(&self._receipt_file, WORKER_NONCE_FILE_SDDL)?;
        let bytes = self.receipt.canonical_bytes(capsule)?;
        let identity = file_identity(&self._receipt_file)?;
        if nonce_root_identity.volume_serial != self.nonce_root_volume_serial
            || nonce_root_identity.file_id != self.nonce_root_file_id
            || nonce_root_identity.attributes & FILE_ATTRIBUTE_DIRECTORY == 0
            || nonce_root_identity.attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
            || identity != self.file_identity
            || read_held_file_bounded(&self._receipt_file, MAX_PROTECTED_RECEIPT_BYTES)? != bytes
            || <[u8; 32]>::from(Sha256::digest(&bytes)) != self.bytes_sha256
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_nonce_receipt_readback_mismatch",
            ));
        }
        Ok((
            self.receipt.digest()?,
            self.receipt.full_readback_receipt_sha256()?,
            self.bytes_sha256,
            identity.volume_serial,
            identity.file_id,
        ))
    }

    fn reopen_readonly_before_seal_ready(
        self,
        layout: &AuthorityLayout,
        capsule: &MaintenanceWorkerCapsule,
    ) -> Result<Self, AuthorityMaintenanceError> {
        replace_writer_lease_after_readonly_reopen(
            self,
            || open_native_worker_nonce(layout, capsule),
            |writer, readonly| {
                let writer_binding = writer.durable_binding(capsule)?;
                let readonly_binding = readonly.durable_binding(capsule)?;
                if writer.receipt != readonly.receipt
                    || writer.nonce_root_volume_serial != readonly.nonce_root_volume_serial
                    || writer.nonce_root_file_id != readonly.nonce_root_file_id
                    || writer.file_identity != readonly.file_identity
                    || writer.bytes_sha256 != readonly.bytes_sha256
                    || writer_binding != readonly_binding
                {
                    return Err(AuthorityMaintenanceError(
                        "authority_worker_nonce_readonly_reopen_mismatch",
                    ));
                }
                Ok(())
            },
        )
    }
}

fn replace_writer_lease_after_readonly_reopen<T>(
    writer: T,
    reopen: impl FnOnce() -> Result<T, AuthorityMaintenanceError>,
    verify_same: impl FnOnce(&T, &T) -> Result<(), AuthorityMaintenanceError>,
) -> Result<T, AuthorityMaintenanceError> {
    let readonly = reopen()?;
    verify_same(&writer, &readonly)?;
    // This explicit boundary is the security property: no write-capable file
    // handle or FILE_ADD_FILE directory handle survives the returned lease.
    drop(writer);
    Ok(readonly)
}

impl NativeCandidateConsumptionLease {
    pub(super) fn bytes(&self) -> &[u8] {
        &self.bytes
    }

    pub(super) fn durable_identity_with_link_count(&self) -> (u64, [u8; 16], u32, [u8; 32]) {
        (
            self.file_identity.volume_serial,
            self.file_identity.file_id,
            self.file_identity.link_count,
            self.bytes_sha256,
        )
    }
}

pub(super) struct NativeWorkerBootstrapStore {
    _binary_chain: Vec<OwnedHandle>,
    _state_chain: Vec<OwnedHandle>,
    binary_directory: OwnedHandle,
    state_directory: OwnedHandle,
    _worker_file: OwnedHandle,
    _capsule_file: OwnedHandle,
    _intent_file: OwnedHandle,
    _bootstrap_receipt_file: OwnedHandle,
    recovery_receipt_files: Vec<OwnedHandle>,
    nonce_consumption_lease: Option<NativeNonceConsumptionLease>,
    journal_file: OwnedHandle,
    launch: MaintenanceWorkerLaunchContract,
    intent: WorkerBootstrapIntentReceipt,
    bootstrap: WorkerBootstrapStagingReceipt,
    recovery_receipts: NativeWorkerRecoveryReceipts,
    candidate_prepared: Option<NativeCandidateCredentialLease>,
    candidate_armed: Option<NativeCandidateCredentialLease>,
    source_store: Option<NativeWorkerSourceStore>,
    records: Vec<MaintenanceWorkerJournalRecord>,
    journal_torn_tail: bool,
    recovery_requires_containment: bool,
}

fn transaction_binding_recovery_is_clean(
    journal_torn_tail: bool,
    recovery_requires_containment: bool,
) -> bool {
    !journal_torn_tail && !recovery_requires_containment
}

impl NativeWorkerBootstrapStore {
    pub(super) fn launch(&self) -> &MaintenanceWorkerLaunchContract {
        &self.launch
    }

    pub(super) fn intent(&self) -> &WorkerBootstrapIntentReceipt {
        &self.intent
    }

    pub(super) fn bootstrap(&self) -> &WorkerBootstrapStagingReceipt {
        &self.bootstrap
    }

    pub(super) fn records(&self) -> &[MaintenanceWorkerJournalRecord] {
        &self.records
    }

    pub(super) fn worker_started_receipt(&self) -> Option<&WorkerStartedReceipt> {
        self.recovery_receipts.worker_started.as_ref()
    }

    pub(super) fn service_created_receipt(&self) -> Option<&ServiceCreatedReceipt> {
        self.recovery_receipts.service_created.as_ref()
    }

    pub(super) fn transaction_started_receipt(&self) -> Option<&TransactionStartedReceipt> {
        self.recovery_receipts.transaction_started.as_ref()
    }

    pub(super) fn exit_ready_receipt(&self) -> Option<&WorkerExitReadyReceipt> {
        self.recovery_receipts.exit_ready.as_ref()
    }

    pub(super) fn staging_cleanup_receipt(&self) -> Option<&WorkerStagingCleanupReceipt> {
        self.recovery_receipts.cleanup.as_ref()
    }

    pub(super) fn service_delete_pending_receipt(&self) -> Option<&ServiceDeletePendingReceipt> {
        self.recovery_receipts.delete_pending.as_ref()
    }

    pub(super) fn service_delete_intent_receipt(&self) -> Option<&ServiceDeleteIntentReceipt> {
        self.recovery_receipts.delete_intent.as_ref()
    }

    pub(super) fn finalizer_handles_closed_receipt(
        &self,
    ) -> Option<&FinalizerHandlesClosedReceipt> {
        self.recovery_receipts.handles_closed.as_ref()
    }

    pub(super) fn service_absent_receipt(&self) -> Option<&ServiceAbsentReceipt> {
        self.recovery_receipts.service_absent.as_ref()
    }

    pub(super) fn reopen_nonce_consumption_readonly_before_seal_ready(
        &mut self,
        layout: &AuthorityLayout,
        capsule: &MaintenanceWorkerCapsule,
    ) -> Result<(), AuthorityMaintenanceError> {
        let writer = self
            .nonce_consumption_lease
            .take()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_nonce_consumption_not_held",
            ))?;
        let readonly = writer.reopen_readonly_before_seal_ready(layout, capsule)?;
        self.nonce_consumption_lease = Some(readonly);
        Ok(())
    }

    pub(super) fn retirement_transaction_receipt_binding(
        &self,
        capsule: &MaintenanceWorkerCapsule,
    ) -> Result<NativeRetirementTransactionReceiptBinding, AuthorityMaintenanceError> {
        if capsule.operation() != AuthorityMaintenanceOperation::Retire
            || self.journal_requires_containment()
            || self.candidate_prepared.is_some()
            || self.candidate_armed.is_some()
            || self.recovery_receipts.candidate_credential_armed.is_some()
            || self
                .records
                .iter()
                .any(|record| record.phase() == MaintenanceWorkerPhase::CandidateCredentialArmed)
        {
            return Err(AuthorityMaintenanceError(
                "authority_retirement_transaction_binding_invalid",
            ));
        }
        let source_bound = self
            .records
            .iter()
            .find(|record| record.phase() == MaintenanceWorkerPhase::SourceHandlesBound)
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_missing",
            ))?;
        let staging = self
            .recovery_receipts
            .staging
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_missing",
            ))?;
        let transaction_started = self.recovery_receipts.transaction_started.as_ref().ok_or(
            AuthorityMaintenanceError("authority_worker_transaction_receipt_not_persisted"),
        )?;
        let transaction_record = self
            .records
            .iter()
            .find(|record| record.phase() == MaintenanceWorkerPhase::TransactionStarted)
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_missing",
            ))?;
        let worker_started =
            self.recovery_receipts
                .worker_started
                .as_ref()
                .ok_or(AuthorityMaintenanceError(
                    "authority_worker_started_receipt_not_persisted",
                ))?;
        let nonce = self
            .nonce_consumption_lease
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_nonce_consumption_not_held",
            ))?;
        if self.recovery_receipts.nonce_consumption.as_ref() != Some(nonce.receipt())
            || self.records.last() != Some(transaction_record)
            || transaction_record.phase_receipt_sha256()? != transaction_started.digest()?
        {
            return Err(AuthorityMaintenanceError(
                "authority_retirement_transaction_binding_mismatch",
            ));
        }
        transaction_started.validate(capsule, source_bound, staging, nonce.receipt())?;
        worker_started.validate(
            capsule,
            &self.bootstrap,
            self.recovery_receipts
                .service_created
                .as_ref()
                .ok_or(AuthorityMaintenanceError(
                    "authority_worker_recovery_evidence_missing",
                ))?,
            self.recovery_receipts
                .pipe
                .as_ref()
                .ok_or(AuthorityMaintenanceError(
                    "authority_worker_recovery_evidence_missing",
                ))?,
            self.recovery_receipts
                .handoff
                .as_ref()
                .ok_or(AuthorityMaintenanceError(
                    "authority_worker_recovery_evidence_missing",
                ))?,
        )?;
        let (
            nonce_consumption_receipt_sha256,
            nonce_consumption_full_readback_sha256,
            nonce_consumption_file_sha256,
            nonce_consumption_file_volume_serial,
            nonce_consumption_file_id,
        ) = nonce.durable_binding(capsule)?;
        let binding = NativeRetirementTransactionReceiptBinding {
            capsule_sha256: capsule.digest()?,
            plan_sha256: capsule.plan_sha256()?,
            generation_sha256: capsule.generation()?,
            transaction_sha256: capsule.transaction_sha256()?,
            source_handles_bound_record_sha256: source_bound.record_sha256()?,
            source_staging_receipt_sha256: staging.digest()?,
            transaction_started_receipt_sha256: transaction_started.digest()?,
            transaction_started_journal_record_sha256: transaction_record.record_sha256()?,
            transaction_started_journal_sequence: transaction_record.sequence(),
            worker_started_receipt_sha256: worker_started.digest()?,
            nonce_consumption_receipt_sha256,
            nonce_consumption_full_readback_sha256,
            nonce_consumption_file_sha256,
            nonce_consumption_file_volume_serial,
            nonce_consumption_file_id,
        };
        if [
            binding.capsule_sha256,
            binding.plan_sha256,
            binding.generation_sha256,
            binding.transaction_sha256,
            binding.source_handles_bound_record_sha256,
            binding.source_staging_receipt_sha256,
            binding.transaction_started_receipt_sha256,
            binding.transaction_started_journal_record_sha256,
            binding.worker_started_receipt_sha256,
            binding.nonce_consumption_receipt_sha256,
            binding.nonce_consumption_full_readback_sha256,
            binding.nonce_consumption_file_sha256,
        ]
        .iter()
        .any(|digest| digest.iter().all(|value| *value == 0))
            || binding.transaction_started_journal_sequence == 0
            || binding.nonce_consumption_file_volume_serial == 0
            || binding
                .nonce_consumption_file_id
                .iter()
                .all(|value| *value == 0)
        {
            return Err(AuthorityMaintenanceError(
                "authority_retirement_transaction_binding_invalid",
            ));
        }
        Ok(binding)
    }

    pub(super) fn candidate_activation_receipt_binding(
        &self,
        capsule: &MaintenanceWorkerCapsule,
    ) -> Result<NativeCandidateActivationReceiptBinding, AuthorityMaintenanceError> {
        if self.journal_requires_containment() {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_activation_receipt_binding_invalid",
            ));
        }
        let transaction_started = self.recovery_receipts.transaction_started.as_ref().ok_or(
            AuthorityMaintenanceError("authority_worker_transaction_receipt_not_persisted"),
        )?;
        let worker_started =
            self.recovery_receipts
                .worker_started
                .as_ref()
                .ok_or(AuthorityMaintenanceError(
                    "authority_worker_started_receipt_not_persisted",
                ))?;
        let prepared = self
            .candidate_prepared
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_candidate_prepared_credential_not_persisted",
            ))?
            .record();
        let armed = self
            .candidate_armed
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_candidate_armed_credential_not_persisted",
            ))?
            .record();
        let armed_receipt = self
            .recovery_receipts
            .candidate_credential_armed
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_candidate_armed_receipt_not_persisted",
            ))?;
        let armed_journal = self
            .records
            .last()
            .filter(|record| record.phase() == MaintenanceWorkerPhase::CandidateCredentialArmed)
            .ok_or(AuthorityMaintenanceError(
                "authority_candidate_armed_journal_not_current",
            ))?;
        let nonce = self
            .nonce_consumption_lease
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_nonce_consumption_not_held",
            ))?;
        if self.recovery_receipts.nonce_consumption.as_ref() != Some(nonce.receipt()) {
            return Err(AuthorityMaintenanceError(
                "authority_worker_nonce_consumption_binding_mismatch",
            ));
        }
        let (
            nonce_consumption_receipt_sha256,
            nonce_consumption_full_readback_sha256,
            nonce_consumption_file_sha256,
            nonce_consumption_file_volume_serial,
            nonce_consumption_file_id,
        ) = nonce.durable_binding(capsule)?;
        armed_receipt.validate(capsule, transaction_started, worker_started, prepared)?;
        let prepared_binding = prepared
            .binding()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        let armed_binding = armed
            .binding()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        let issuer = prepared_binding.issuer();
        let candidate_service =
            armed
                .candidate_service()
                .copied()
                .ok_or(AuthorityMaintenanceError(
                    "authority_candidate_armed_service_evidence_missing",
                ))?;
        let armed_receipt_sha256 = armed_receipt.digest()?;
        if prepared.phase() != CandidateCredentialPhase::Prepared
            || armed.phase() != CandidateCredentialPhase::Armed
            || prepared_binding != armed_binding
            || prepared
                .credential_sha256()
                .map_err(|error| AuthorityMaintenanceError(error.code()))?
                != armed
                    .credential_sha256()
                    .map_err(|error| AuthorityMaintenanceError(error.code()))?
            || armed
                .armed_receipt_sha256()
                .map_err(|error| AuthorityMaintenanceError(error.code()))?
                != armed_receipt_sha256
            || armed_receipt.prepared_record_sha256()?
                != prepared
                    .record_sha256()
                    .map_err(|error| AuthorityMaintenanceError(error.code()))?
            || armed_receipt.maintenance_worker() != issuer.maintenance_worker()
            || armed_receipt.candidate_service() != &candidate_service
            || armed_journal.phase_receipt_sha256()? != armed_receipt_sha256
            || *issuer.capsule_sha256() != capsule.digest()?
            || *issuer.transaction_started_receipt_sha256() != transaction_started.digest()?
            || *issuer.worker_started_receipt_sha256() != worker_started.digest()?
            || *issuer.nonce_consumption_receipt_sha256() != nonce_consumption_receipt_sha256
            || *issuer.nonce_consumption_full_readback_sha256()
                != nonce_consumption_full_readback_sha256
            || *issuer.nonce_consumption_file_sha256() != nonce_consumption_file_sha256
            || issuer.nonce_consumption_file_volume_serial() != nonce_consumption_file_volume_serial
            || *issuer.nonce_consumption_file_id() != nonce_consumption_file_id
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_activation_receipt_binding_mismatch",
            ));
        }
        let binding = NativeCandidateActivationReceiptBinding {
            capsule_sha256: capsule.digest()?,
            plan_sha256: capsule.plan_sha256()?,
            generation_sha256: capsule.generation()?,
            transaction_sha256: capsule.transaction_sha256()?,
            transaction_started_receipt_sha256: transaction_started.digest()?,
            worker_started_receipt_sha256: worker_started.digest()?,
            maintenance_worker: *armed_receipt.maintenance_worker(),
            candidate_credential_sha256: armed
                .credential_sha256()
                .map_err(|error| AuthorityMaintenanceError(error.code()))?,
            candidate_credential_armed_record_sha256: armed
                .record_sha256()
                .map_err(|error| AuthorityMaintenanceError(error.code()))?,
            candidate_credential_armed_receipt_sha256: armed_receipt_sha256,
            candidate_credential_armed_journal_record_sha256: armed_journal.record_sha256()?,
            candidate_credential_armed_journal_sequence: armed_journal.sequence(),
            candidate_service,
            nonce_consumption_receipt_sha256,
            nonce_consumption_full_readback_sha256,
            nonce_consumption_file_sha256,
            nonce_consumption_file_volume_serial,
            nonce_consumption_file_id,
        };
        if binding.capsule_sha256.iter().all(|value| *value == 0)
            || binding.plan_sha256.iter().all(|value| *value == 0)
            || binding.generation_sha256.iter().all(|value| *value == 0)
            || binding.transaction_sha256.iter().all(|value| *value == 0)
            || binding
                .transaction_started_receipt_sha256
                .iter()
                .all(|value| *value == 0)
            || binding
                .worker_started_receipt_sha256
                .iter()
                .all(|value| *value == 0)
            || binding
                .candidate_credential_sha256
                .iter()
                .all(|value| *value == 0)
            || binding
                .candidate_credential_armed_record_sha256
                .iter()
                .all(|value| *value == 0)
            || binding
                .candidate_credential_armed_receipt_sha256
                .iter()
                .all(|value| *value == 0)
            || binding
                .candidate_credential_armed_journal_record_sha256
                .iter()
                .all(|value| *value == 0)
            || binding.candidate_credential_armed_journal_sequence == 0
            || binding
                .nonce_consumption_receipt_sha256
                .iter()
                .all(|value| *value == 0)
            || binding
                .nonce_consumption_full_readback_sha256
                .iter()
                .all(|value| *value == 0)
            || binding
                .nonce_consumption_file_sha256
                .iter()
                .all(|value| *value == 0)
            || binding.nonce_consumption_file_volume_serial == 0
            || binding
                .nonce_consumption_file_id
                .iter()
                .all(|value| *value == 0)
            || binding.maintenance_worker.validate().is_err()
            || binding.candidate_service.validate().is_err()
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_activation_receipt_binding_invalid",
            ));
        }
        Ok(binding)
    }

    pub(super) fn release_external_staging_readback_handles(
        &mut self,
    ) -> Result<(), AuthorityMaintenanceError> {
        if self.recovery_receipts.staging.is_none() || self.source_store.is_none() {
            return Err(AuthorityMaintenanceError(
                "authority_worker_staging_readback_handle_state_invalid",
            ));
        }
        drop(self.source_store.take());
        Ok(())
    }

    pub(super) fn release_reopened_staging_readback_handles(
        &mut self,
    ) -> Result<(), AuthorityMaintenanceError> {
        if self.recovery_receipts.staging.is_none() {
            return Err(AuthorityMaintenanceError(
                "authority_worker_staging_readback_handle_state_invalid",
            ));
        }
        drop(self.source_store.take());
        Ok(())
    }

    pub(super) fn persist_pipe_prepared_receipt(
        &mut self,
        layout: &AuthorityLayout,
        capsule: &MaintenanceWorkerCapsule,
        pipe: &WorkerPipePreparedReceipt,
    ) -> Result<NativePersistedPipePrepared, AuthorityMaintenanceError> {
        pipe.validate(capsule, &self.launch)?;
        let bytes = pipe.canonical_bytes(capsule, &self.launch)?;
        if self.recovery_receipts.original_pipe.is_some()
            || self
                .records
                .last()
                .map(MaintenanceWorkerJournalRecord::phase)
                != Some(MaintenanceWorkerPhase::CapsuleStaged)
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_pipe_prepared_phase_invalid",
            ));
        }
        let persisted = persist_or_reopen_sealed_receipt(
            &self.state_directory,
            PIPE_PREPARED_RECEIPT_FILE_NAME,
            &bytes,
            pipe.clone(),
        )?;
        require_expected_leaf_path(
            &persisted._receipt_file,
            &layout
                .maintenance_worker_state_root(&capsule.digest()?)
                .map_err(|_| AuthorityMaintenanceError("authority_worker_path_invalid"))?
                .join(PIPE_PREPARED_RECEIPT_FILE_NAME),
        )?;
        let record = authorize_pipe_prepared(capsule, &self.launch, &self.records, pipe)?;
        self.append_journal_record(capsule, record)?;
        self.recovery_receipts.original_pipe = Some(pipe.clone());
        self.recovery_receipts.pipe = Some(pipe.clone());
        Ok(persisted)
    }

    pub(super) fn persist_rebuilt_pipe_recovery(
        &mut self,
        layout: &AuthorityLayout,
        capsule: &MaintenanceWorkerCapsule,
        prior: &WorkerPipePreparedReceipt,
        recovery: WorkerPipeRecoveryReceipt,
    ) -> Result<NativePersistedPipePrepared, AuthorityMaintenanceError> {
        recovery.validate(capsule, &self.launch, &self.records, prior)?;
        if self.recovery_receipts.original_pipe.as_ref() != Some(prior)
            || self.recovery_receipts.pipe_recovery.is_some()
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_pipe_recovery_phase_invalid",
            ));
        }
        let replacement = recovery.replacement_pipe().clone();
        let persisted = persist_or_reopen_sealed_receipt(
            &self.state_directory,
            PIPE_RECOVERY_RECEIPT_FILE_NAME,
            &recovery.sealed_canonical_bytes()?,
            recovery.clone(),
        )?;
        require_worker_receipt_path(
            layout,
            capsule,
            &persisted._receipt_file,
            PIPE_RECOVERY_RECEIPT_FILE_NAME,
        )?;
        let record =
            authorize_pipe_recovered(capsule, &self.launch, &self.records, prior, &recovery)?;
        self.append_journal_record(capsule, record)?;
        let NativePersistedReceipt {
            _receipt_file,
            file_identity,
            bytes_sha256,
            ..
        } = persisted;
        self.recovery_receipts.pipe_recovery = Some(recovery);
        self.recovery_receipts.pipe = Some(replacement.clone());
        Ok(NativePersistedReceipt {
            _receipt_file,
            receipt: replacement,
            file_identity,
            bytes_sha256,
        })
    }

    pub(super) fn record_service_created(
        &mut self,
        layout: &AuthorityLayout,
        capsule: &MaintenanceWorkerCapsule,
        pipe: NativePersistedPipePrepared,
        receipt: ServiceCreatedReceipt,
    ) -> Result<(), AuthorityMaintenanceError> {
        receipt.validate(capsule, &self.launch, &self.bootstrap, pipe.receipt())?;
        let bytes = receipt.sealed_canonical_bytes()?;
        let persisted = persist_or_reopen_sealed_receipt(
            &self.state_directory,
            SERVICE_CREATED_RECEIPT_FILE_NAME,
            &bytes,
            receipt.clone(),
        )?;
        require_worker_receipt_path(
            layout,
            capsule,
            &persisted._receipt_file,
            SERVICE_CREATED_RECEIPT_FILE_NAME,
        )?;
        if self.recovery_receipts.pipe.as_ref() != Some(pipe.receipt()) {
            return Err(AuthorityMaintenanceError(
                "authority_worker_pipe_receipt_not_persisted",
            ));
        }
        let pipe_phase_receipt_sha256 = match self.records.last().map(|record| record.phase()) {
            Some(MaintenanceWorkerPhase::PipePrepared) => pipe.receipt().digest()?,
            Some(MaintenanceWorkerPhase::PipeRecovered) => self
                .recovery_receipts
                .pipe_recovery
                .as_ref()
                .ok_or(AuthorityMaintenanceError(
                    "authority_worker_pipe_recovery_receipt_not_persisted",
                ))?
                .digest()?,
            _ => {
                return Err(AuthorityMaintenanceError(
                    "authority_worker_service_created_phase_invalid",
                ))
            }
        };
        let record = authorize_service_created_after_pipe(
            capsule,
            &self.launch,
            &self.bootstrap,
            pipe.receipt(),
            &self.records,
            pipe_phase_receipt_sha256,
            &receipt,
        )?;
        self.append_journal_record(capsule, record)?;
        self.recovery_receipt_files.push(pipe._receipt_file);
        self.recovery_receipt_files.push(persisted._receipt_file);
        self.recovery_receipts.pipe = Some(pipe.receipt);
        self.recovery_receipts.service_created = Some(receipt);
        Ok(())
    }

    pub(super) fn persist_handoff_receipt(
        &mut self,
        layout: &AuthorityLayout,
        capsule: &MaintenanceWorkerCapsule,
        handoff: WorkerHandleHandoffReceipt,
    ) -> Result<(), AuthorityMaintenanceError> {
        let pipe = self
            .recovery_receipts
            .pipe
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_pipe_receipt_not_persisted",
            ))?;
        handoff.validate_with_pipe(capsule, &self.launch, pipe)?;
        let bytes = handoff.canonical_bytes(capsule)?;
        let persisted = persist_or_reopen_sealed_receipt(
            &self.state_directory,
            HANDOFF_RECEIPT_FILE_NAME,
            &bytes,
            handoff.clone(),
        )?;
        require_worker_receipt_path(
            layout,
            capsule,
            &persisted._receipt_file,
            HANDOFF_RECEIPT_FILE_NAME,
        )?;
        self.recovery_receipt_files.push(persisted._receipt_file);
        self.recovery_receipts.handoff = Some(handoff);
        Ok(())
    }

    pub(super) fn claim_first_worker_invocation(
        &mut self,
        layout: &AuthorityLayout,
        capsule: &MaintenanceWorkerCapsule,
        worker: &WorkerProcessBinding,
    ) -> Result<WorkerInvocationClaimReceipt, AuthorityMaintenanceError> {
        let service_created =
            self.recovery_receipts
                .service_created
                .as_ref()
                .ok_or(AuthorityMaintenanceError(
                    "authority_worker_service_receipt_not_persisted",
                ))?;
        let pipe = self
            .recovery_receipts
            .pipe
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_pipe_receipt_not_persisted",
            ))?;
        let handoff = self
            .recovery_receipts
            .handoff
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_handoff_receipt_not_persisted",
            ))?;
        if self.recovery_receipts.invocation_claim.is_some()
            || self.recovery_receipts.worker_started.is_some()
            || self
                .records
                .last()
                .map(MaintenanceWorkerJournalRecord::phase)
                != Some(MaintenanceWorkerPhase::ServiceCreated)
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_invocation_already_claimed",
            ));
        }
        let claim = WorkerInvocationClaimReceipt::new(
            capsule,
            &self.launch,
            &self.bootstrap,
            service_created,
            pipe,
            handoff,
            worker,
        )?;
        let receipt_file = create_relative_file_exact(
            &self.state_directory,
            WORKER_INVOCATION_CLAIM_RECEIPT_FILE_NAME,
            STATE_FILE_SDDL,
            &claim.sealed_canonical_bytes()?,
            FILE_SHARE_READ,
        )?;
        require_worker_receipt_path(
            layout,
            capsule,
            &receipt_file,
            WORKER_INVOCATION_CLAIM_RECEIPT_FILE_NAME,
        )?;
        let record = authorize_worker_invocation_claimed(
            capsule,
            &self.launch,
            &self.bootstrap,
            service_created,
            pipe,
            handoff,
            worker,
            &self.records,
            &claim,
        )?;
        self.append_journal_record(capsule, record)?;
        self.recovery_receipt_files.push(receipt_file);
        self.recovery_receipts.invocation_claim = Some(claim.clone());
        Ok(claim)
    }

    pub(super) fn record_worker_started(
        &mut self,
        layout: &AuthorityLayout,
        capsule: &MaintenanceWorkerCapsule,
        receipt: WorkerStartedReceipt,
    ) -> Result<(), AuthorityMaintenanceError> {
        let service_created = self
            .recovery_receipts
            .service_created
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_service_receipt_not_persisted",
            ))?
            .clone();
        let pipe = self
            .recovery_receipts
            .pipe
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_pipe_receipt_not_persisted",
            ))?
            .clone();
        let handoff = self
            .recovery_receipts
            .handoff
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_handoff_receipt_not_persisted",
            ))?
            .clone();
        receipt.validate(capsule, &self.bootstrap, &service_created, &pipe, &handoff)?;
        let persisted = persist_or_reopen_sealed_receipt(
            &self.state_directory,
            WORKER_STARTED_RECEIPT_FILE_NAME,
            &receipt.sealed_canonical_bytes()?,
            receipt.clone(),
        )?;
        require_worker_receipt_path(
            layout,
            capsule,
            &persisted._receipt_file,
            WORKER_STARTED_RECEIPT_FILE_NAME,
        )?;
        let record = if let Some(claim) = self.recovery_receipts.invocation_claim.as_ref() {
            authorize_claimed_worker_started(
                capsule,
                &self.launch,
                &self.bootstrap,
                &service_created,
                &pipe,
                &handoff,
                claim,
                &self.records,
                &receipt,
            )?
        } else {
            authorize_worker_started(
                capsule,
                &self.bootstrap,
                &service_created,
                &pipe,
                &handoff,
                &self.records,
                &receipt,
            )?
        };
        self.append_journal_record(capsule, record)?;
        self.recovery_receipt_files.push(persisted._receipt_file);
        self.recovery_receipts.worker_started = Some(receipt);
        Ok(())
    }

    pub(super) fn claim_and_record_current_worker_started(
        &mut self,
        layout: &AuthorityLayout,
        capsule: &MaintenanceWorkerCapsule,
        worker: &WorkerProcessBinding,
        worker_local_system: bool,
        worker_high_integrity: bool,
        worker_session_id: u32,
    ) -> Result<WorkerStartedReceipt, AuthorityMaintenanceError> {
        self.claim_first_worker_invocation(layout, capsule, worker)?;
        let service_created =
            self.recovery_receipts
                .service_created
                .as_ref()
                .ok_or(AuthorityMaintenanceError(
                    "authority_worker_service_receipt_not_persisted",
                ))?;
        let pipe = self
            .recovery_receipts
            .pipe
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_pipe_receipt_not_persisted",
            ))?;
        let handoff = self
            .recovery_receipts
            .handoff
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_handoff_receipt_not_persisted",
            ))?;
        let receipt = WorkerStartedReceipt::from_observed(
            capsule,
            &self.bootstrap,
            service_created,
            pipe,
            handoff,
            worker_local_system,
            worker_high_integrity,
            worker_session_id,
        )?;
        self.record_worker_started(layout, capsule, receipt.clone())?;
        Ok(receipt)
    }

    pub(super) fn refresh_after_external_worker_staging(
        &mut self,
        layout: &AuthorityLayout,
        capsule: &MaintenanceWorkerCapsule,
        staging_frame: &[u8],
    ) -> Result<DurableSourceStagingReceipt, AuthorityMaintenanceError> {
        let journal_bytes =
            read_held_file_bounded(&self.journal_file, MAX_PROTECTED_RECEIPT_BYTES)?;
        let recovered = parse_worker_journal_recovery(&journal_bytes, capsule.digest()?)?;
        let records = recovered.records();
        if recovered.torn_tail()
            || records.len() <= self.records.len()
            || !records.starts_with(&self.records)
            || records.last().map(MaintenanceWorkerJournalRecord::phase)
                != Some(MaintenanceWorkerPhase::SourceHandlesBound)
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_external_staging_journal_invalid",
            ));
        }
        let handoff = self
            .recovery_receipts
            .handoff
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_handoff_receipt_not_persisted",
            ))?
            .clone();
        let service_created = self
            .recovery_receipts
            .service_created
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_service_receipt_not_persisted",
            ))?
            .clone();
        let pipe = self
            .recovery_receipts
            .pipe
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_pipe_receipt_not_persisted",
            ))?
            .clone();

        let (claim_file, claim_bytes) = open_worker_receipt_file(
            layout,
            &self.state_directory,
            capsule,
            WORKER_INVOCATION_CLAIM_RECEIPT_FILE_NAME,
        )?;
        let claim = WorkerInvocationClaimReceipt::parse_sealed_canonical(&claim_bytes)?;
        claim.validate(
            capsule,
            &self.launch,
            &self.bootstrap,
            &service_created,
            &pipe,
            &handoff,
            handoff.worker(),
        )?;
        let claim_record = records
            .iter()
            .find(|record| record.phase() == MaintenanceWorkerPhase::WorkerInvocationClaimed)
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_external_staging_journal_invalid",
            ))?;
        if claim_record.phase_receipt_sha256()? != claim.digest()? {
            return Err(AuthorityMaintenanceError(
                "authority_worker_external_staging_receipt_mismatch",
            ));
        }

        let (started_file, started_bytes) = open_worker_receipt_file(
            layout,
            &self.state_directory,
            capsule,
            WORKER_STARTED_RECEIPT_FILE_NAME,
        )?;
        let worker_started = WorkerStartedReceipt::parse_sealed_canonical(&started_bytes)?;
        worker_started.validate(capsule, &self.bootstrap, &service_created, &pipe, &handoff)?;
        let worker_started_record = records
            .iter()
            .find(|record| record.phase() == MaintenanceWorkerPhase::WorkerStarted)
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_external_staging_journal_invalid",
            ))?;
        if worker_started_record.phase_receipt_sha256()? != worker_started.digest()? {
            return Err(AuthorityMaintenanceError(
                "authority_worker_external_staging_receipt_mismatch",
            ));
        }

        let (intent_file, intent_bytes) = open_worker_receipt_file(
            layout,
            &self.state_directory,
            capsule,
            SOURCE_STAGING_INTENT_RECEIPT_FILE_NAME,
        )?;
        let staging_intent =
            WorkerSourceStagingIntentReceipt::parse_sealed_canonical(&intent_bytes)?;
        staging_intent.validate(capsule, worker_started_record, &handoff)?;
        let staging_intent_record = records
            .iter()
            .find(|record| record.phase() == MaintenanceWorkerPhase::SourceStagingIntent)
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_external_staging_journal_invalid",
            ))?;
        if staging_intent_record.phase_receipt_sha256()? != staging_intent.digest()? {
            return Err(AuthorityMaintenanceError(
                "authority_worker_external_staging_receipt_mismatch",
            ));
        }

        let (staging_file, staging_bytes) = open_worker_receipt_file(
            layout,
            &self.state_directory,
            capsule,
            SOURCE_STAGING_RECEIPT_FILE_NAME,
        )?;
        if staging_bytes != staging_frame {
            return Err(AuthorityMaintenanceError(
                "authority_worker_external_staging_frame_mismatch",
            ));
        }
        let staging = DurableSourceStagingReceipt::parse_canonical(
            &staging_bytes,
            capsule,
            worker_started_record,
            &handoff,
        )?;
        let source_bound_record = records.last().ok_or(AuthorityMaintenanceError(
            "authority_worker_external_staging_journal_invalid",
        ))?;
        if source_bound_record.phase_receipt_sha256()? != staging.digest()? {
            return Err(AuthorityMaintenanceError(
                "authority_worker_external_staging_receipt_mismatch",
            ));
        }
        let source_store = open_native_worker_source_store(
            layout,
            &self.state_directory,
            capsule,
            worker_started_record,
            &handoff,
            &staging,
        )?;
        staging.validate_identity_ledger(
            capsule,
            worker_started_record,
            &handoff,
            &source_store.identity_ledger,
        )?;

        self.records = records.to_vec();
        self.recovery_receipt_files
            .extend([claim_file, started_file, intent_file, staging_file]);
        self.recovery_receipts.invocation_claim = Some(claim);
        self.recovery_receipts.worker_started = Some(worker_started);
        self.recovery_receipts.staging_intent = Some(staging_intent);
        self.recovery_receipts.staging = Some(staging.clone());
        self.source_store = Some(source_store);
        Ok(staging)
    }

    pub(super) fn record_transaction_committed(
        &mut self,
        layout: &AuthorityLayout,
        capsule: &MaintenanceWorkerCapsule,
        receipt: TransactionCommittedReceipt,
    ) -> Result<(), AuthorityMaintenanceError> {
        let started = self
            .recovery_receipts
            .transaction_started
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_transaction_receipt_not_persisted",
            ))?
            .clone();
        receipt.validate(capsule, &started)?;
        let persisted = persist_or_reopen_sealed_receipt(
            &self.state_directory,
            TRANSACTION_COMMITTED_RECEIPT_FILE_NAME,
            &receipt.sealed_canonical_bytes()?,
            receipt.clone(),
        )?;
        require_worker_receipt_path(
            layout,
            capsule,
            &persisted._receipt_file,
            TRANSACTION_COMMITTED_RECEIPT_FILE_NAME,
        )?;
        let record = match self.recovery_receipts.candidate_credential_armed.as_ref() {
            Some(armed) => authorize_transaction_committed_after_candidate_armed(
                capsule,
                &self.records,
                &started,
                armed,
                &receipt,
            )?,
            None => authorize_transaction_committed(capsule, &self.records, &started, &receipt)?,
        };
        self.append_journal_record(capsule, record)?;
        self.recovery_receipt_files.push(persisted._receipt_file);
        self.recovery_receipts.transaction_committed = Some(receipt);
        Ok(())
    }

    pub(super) fn record_transaction_contained(
        &mut self,
        layout: &AuthorityLayout,
        capsule: &MaintenanceWorkerCapsule,
        receipt: TransactionContainedReceipt,
    ) -> Result<(), AuthorityMaintenanceError> {
        let started = self
            .recovery_receipts
            .transaction_started
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_transaction_receipt_not_persisted",
            ))?
            .clone();
        receipt.validate(capsule, &started)?;
        let persisted = persist_or_reopen_sealed_receipt(
            &self.state_directory,
            TRANSACTION_CONTAINED_RECEIPT_FILE_NAME,
            &receipt.sealed_canonical_bytes()?,
            receipt.clone(),
        )?;
        require_worker_receipt_path(
            layout,
            capsule,
            &persisted._receipt_file,
            TRANSACTION_CONTAINED_RECEIPT_FILE_NAME,
        )?;
        let record = match self.recovery_receipts.candidate_credential_armed.as_ref() {
            Some(armed) => authorize_transaction_contained_after_candidate_armed(
                capsule,
                &self.records,
                &started,
                armed,
                &receipt,
            )?,
            None => authorize_transaction_contained(capsule, &self.records, &started, &receipt)?,
        };
        self.append_journal_record(capsule, record)?;
        self.recovery_receipt_files.push(persisted._receipt_file);
        self.recovery_receipts.transaction_contained = Some(receipt);
        Ok(())
    }

    pub(super) fn record_source_stage_resolved(
        &mut self,
        layout: &AuthorityLayout,
        capsule: &MaintenanceWorkerCapsule,
        receipt: WorkerStagingCleanupReceipt,
    ) -> Result<(), AuthorityMaintenanceError> {
        let staging = self
            .recovery_receipts
            .staging
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_staging_receipt_not_persisted",
            ))?
            .clone();
        let terminal = self
            .records
            .last()
            .filter(|record| record.phase().is_transaction_terminal())
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_transaction_terminal_missing",
            ))?
            .clone();
        receipt.validate(capsule, &staging, &terminal)?;
        let persisted = persist_or_reopen_sealed_receipt(
            &self.state_directory,
            STAGING_CLEANUP_RECEIPT_FILE_NAME,
            &receipt.canonical_bytes(capsule, &staging, &terminal)?,
            receipt.clone(),
        )?;
        require_worker_receipt_path(
            layout,
            capsule,
            &persisted._receipt_file,
            STAGING_CLEANUP_RECEIPT_FILE_NAME,
        )?;
        let record = authorize_source_stage_resolved(capsule, &self.records, &staging, &receipt)?;
        self.append_journal_record(capsule, record)?;
        self.recovery_receipt_files.push(persisted._receipt_file);
        self.recovery_receipts.cleanup = Some(receipt);
        Ok(())
    }

    pub(super) fn record_exit_ready(
        &mut self,
        layout: &AuthorityLayout,
        capsule: &MaintenanceWorkerCapsule,
        receipt: WorkerExitReadyReceipt,
    ) -> Result<(), AuthorityMaintenanceError> {
        let cleanup = self
            .recovery_receipts
            .cleanup
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_cleanup_receipt_not_persisted",
            ))?
            .clone();
        let worker_started = self
            .recovery_receipts
            .worker_started
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_started_receipt_not_persisted",
            ))?
            .clone();
        let terminal = self
            .records
            .iter()
            .find(|record| record.phase().is_transaction_terminal())
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_transaction_terminal_missing",
            ))?;
        receipt.validate(capsule, terminal, &cleanup, &worker_started)?;
        let persisted = persist_or_reopen_sealed_receipt(
            &self.state_directory,
            WORKER_EXIT_READY_RECEIPT_FILE_NAME,
            &receipt.sealed_canonical_bytes()?,
            receipt.clone(),
        )?;
        require_worker_receipt_path(
            layout,
            capsule,
            &persisted._receipt_file,
            WORKER_EXIT_READY_RECEIPT_FILE_NAME,
        )?;
        let record = authorize_worker_exit_ready(
            capsule,
            &self.records,
            &cleanup,
            &worker_started,
            &receipt,
        )?;
        self.append_journal_record(capsule, record)?;
        self.recovery_receipt_files.push(persisted._receipt_file);
        self.recovery_receipts.exit_ready = Some(receipt);
        Ok(())
    }

    pub(super) fn record_service_delete_intent_after_exit_ready(
        &mut self,
        layout: &AuthorityLayout,
        capsule: &MaintenanceWorkerCapsule,
        receipt: ServiceDeleteIntentReceipt,
    ) -> Result<(), AuthorityMaintenanceError> {
        let exit_ready = self
            .recovery_receipts
            .exit_ready
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_exit_ready_receipt_not_persisted",
            ))?
            .clone();
        let service_created = self
            .recovery_receipts
            .service_created
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_service_receipt_not_persisted",
            ))?
            .clone();
        receipt.validate(capsule, &self.launch, &service_created, &exit_ready)?;
        let persisted = persist_or_reopen_sealed_receipt(
            &self.state_directory,
            SERVICE_DELETE_INTENT_RECEIPT_FILE_NAME,
            &receipt.sealed_canonical_bytes()?,
            receipt.clone(),
        )?;
        require_worker_receipt_path(
            layout,
            capsule,
            &persisted._receipt_file,
            SERVICE_DELETE_INTENT_RECEIPT_FILE_NAME,
        )?;
        let record = authorize_service_delete_intent_after_exit_ready(
            capsule,
            &self.records,
            &exit_ready,
            &receipt,
        )?;
        self.append_journal_record(capsule, record)?;
        self.recovery_receipt_files.push(persisted._receipt_file);
        self.recovery_receipts.delete_intent = Some(receipt);
        Ok(())
    }

    pub(super) fn record_service_delete_pending_after_intent(
        &mut self,
        layout: &AuthorityLayout,
        capsule: &MaintenanceWorkerCapsule,
        receipt: ServiceDeletePendingReceipt,
    ) -> Result<(), AuthorityMaintenanceError> {
        let terminal_sha256 = self
            .records
            .iter()
            .find(|record| record.phase().is_transaction_terminal())
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_transaction_terminal_missing",
            ))?
            .phase_receipt_sha256()?;
        let delete_intent = self
            .recovery_receipts
            .delete_intent
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_service_delete_intent_receipt_missing",
            ))?
            .clone();
        receipt.validate(capsule, terminal_sha256, &delete_intent)?;
        let persisted = persist_or_reopen_sealed_receipt(
            &self.state_directory,
            SERVICE_DELETE_PENDING_RECEIPT_FILE_NAME,
            &receipt.sealed_canonical_bytes()?,
            receipt.clone(),
        )?;
        require_worker_receipt_path(
            layout,
            capsule,
            &persisted._receipt_file,
            SERVICE_DELETE_PENDING_RECEIPT_FILE_NAME,
        )?;
        let record = authorize_service_delete_pending_after_intent(
            capsule,
            &self.records,
            terminal_sha256,
            &delete_intent,
            &receipt,
        )?;
        self.append_journal_record(capsule, record)?;
        self.recovery_receipt_files.push(persisted._receipt_file);
        self.recovery_receipts.delete_pending = Some(receipt);
        Ok(())
    }

    pub(super) fn record_finalizer_handles_closed(
        &mut self,
        layout: &AuthorityLayout,
        capsule: &MaintenanceWorkerCapsule,
        receipt: FinalizerHandlesClosedReceipt,
    ) -> Result<(), AuthorityMaintenanceError> {
        let exit_ready = self
            .recovery_receipts
            .exit_ready
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_exit_ready_receipt_not_persisted",
            ))?
            .clone();
        let delete_pending = self
            .recovery_receipts
            .delete_pending
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_delete_receipt_not_persisted",
            ))?
            .clone();
        receipt.validate(capsule, &exit_ready, &delete_pending)?;
        let persisted = persist_or_reopen_sealed_receipt(
            &self.state_directory,
            FINALIZER_HANDLES_CLOSED_RECEIPT_FILE_NAME,
            &receipt.sealed_canonical_bytes()?,
            receipt.clone(),
        )?;
        require_worker_receipt_path(
            layout,
            capsule,
            &persisted._receipt_file,
            FINALIZER_HANDLES_CLOSED_RECEIPT_FILE_NAME,
        )?;
        let record = authorize_finalizer_handles_closed(
            capsule,
            &self.records,
            &exit_ready,
            &delete_pending,
            &receipt,
        )?;
        self.append_journal_record(capsule, record)?;
        self.recovery_receipt_files.push(persisted._receipt_file);
        self.recovery_receipts.handles_closed = Some(receipt);
        Ok(())
    }

    pub(super) fn record_service_absent_after_handles_closed(
        &mut self,
        layout: &AuthorityLayout,
        capsule: &MaintenanceWorkerCapsule,
        receipt: ServiceAbsentReceipt,
    ) -> Result<(), AuthorityMaintenanceError> {
        let cleanup = self
            .recovery_receipts
            .cleanup
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_cleanup_receipt_not_persisted",
            ))?
            .clone();
        let delete_pending = self
            .recovery_receipts
            .delete_pending
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_delete_receipt_not_persisted",
            ))?
            .clone();
        let handles_closed = self
            .recovery_receipts
            .handles_closed
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_finalizer_handles_closed_receipt_missing",
            ))?
            .clone();
        receipt.validate(capsule, &delete_pending, &cleanup)?;
        let persisted = persist_or_reopen_sealed_receipt(
            &self.state_directory,
            SERVICE_ABSENT_RECEIPT_FILE_NAME,
            &receipt.sealed_canonical_bytes()?,
            receipt.clone(),
        )?;
        require_worker_receipt_path(
            layout,
            capsule,
            &persisted._receipt_file,
            SERVICE_ABSENT_RECEIPT_FILE_NAME,
        )?;
        let record = authorize_service_absent_after_handles_closed(
            capsule,
            &self.records,
            &cleanup,
            &delete_pending,
            &handles_closed,
            &receipt,
        )?;
        self.append_journal_record(capsule, record)?;
        self.recovery_receipt_files.push(persisted._receipt_file);
        self.recovery_receipts.service_absent = Some(receipt);
        Ok(())
    }

    pub(super) fn journal_requires_containment(&self) -> bool {
        !transaction_binding_recovery_is_clean(
            self.journal_torn_tail,
            self.recovery_requires_containment,
        )
    }

    pub(super) fn append_journal_record(
        &mut self,
        capsule: &MaintenanceWorkerCapsule,
        record: MaintenanceWorkerJournalRecord,
    ) -> Result<(), AuthorityMaintenanceError> {
        if self.journal_torn_tail {
            return Err(AuthorityMaintenanceError(
                "authority_worker_journal_requires_containment",
            ));
        }
        let prior_bytes = read_held_file_bounded(&self.journal_file, MAX_PROTECTED_RECEIPT_BYTES)?;
        let prior = parse_worker_journal_recovery(&prior_bytes, capsule.digest()?)?;
        if prior.torn_tail()
            || prior.records() != self.records
            || prior.durable_byte_length() != prior_bytes.len()
        {
            self.journal_torn_tail = true;
            return Err(AuthorityMaintenanceError(
                "authority_worker_journal_requires_containment",
            ));
        }
        let mut next = self.records.clone();
        next.push(record.clone());
        let expected = encode_worker_journal(capsule.digest()?, &next)?;
        let append = encode_worker_journal_append(capsule.digest()?, &self.records, &record)?;
        if let Err(error) =
            append_held_file_exact(&self.journal_file, prior_bytes.len() as u64, &append)
        {
            self.journal_torn_tail = true;
            return Err(error);
        }
        flush_handle(&self.journal_file, "authority_worker_journal_flush_failed")?;
        flush_handle(
            &self.state_directory,
            "authority_worker_journal_parent_flush_failed",
        )?;
        let readback = read_held_file_bounded(&self.journal_file, MAX_PROTECTED_RECEIPT_BYTES)?;
        let parsed = parse_worker_journal_recovery(&readback, capsule.digest()?)?;
        if readback != expected
            || !readback.starts_with(&prior_bytes)
            || parsed.torn_tail()
            || parsed.records() != next
        {
            self.journal_torn_tail = true;
            return Err(AuthorityMaintenanceError(
                "authority_worker_journal_readback_mismatch",
            ));
        }
        self.records = next;
        Ok(())
    }

    pub(super) fn binary_directory_handle(&self) -> HANDLE {
        self.binary_directory.as_raw_handle().cast()
    }

    pub(super) fn state_directory_handle(&self) -> HANDLE {
        self.state_directory.as_raw_handle().cast()
    }

    pub(super) fn stage_native_worker_sources(
        &mut self,
        layout: &AuthorityLayout,
        capsule: &MaintenanceWorkerCapsule,
        worker_started: &MaintenanceWorkerJournalRecord,
        handoff: &WorkerHandleHandoffReceipt,
        source_handles: [OwnedHandle; PROTECTED_GENERATION_PAYLOAD_COUNT],
    ) -> Result<DurableSourceStagingReceipt, AuthorityMaintenanceError> {
        let stored_handoff =
            self.recovery_receipts
                .handoff
                .as_ref()
                .ok_or(AuthorityMaintenanceError(
                    "authority_worker_handoff_receipt_not_persisted",
                ))?;
        if stored_handoff != handoff
            || self.recovery_receipts.staging_intent.is_some()
            || self.recovery_receipts.staging.is_some()
            || self.source_store.is_some()
            || self.records.last() != Some(worker_started)
            || worker_started.phase() != MaintenanceWorkerPhase::WorkerStarted
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_source_staging_phase_invalid",
            ));
        }
        handoff.validate(capsule)?;
        let received_handle_values = source_handles
            .iter()
            .map(|handle| handle.as_raw_handle() as usize as u64)
            .collect::<Vec<_>>();
        if received_handle_values.as_slice() != handoff.duplicated_target_handle_values() {
            return Err(AuthorityMaintenanceError(
                "authority_worker_duplicated_handle_value_invalid",
            ));
        }

        let staging_intent =
            WorkerSourceStagingIntentReceipt::new(capsule, worker_started, handoff)?;
        let persisted_intent = persist_or_reopen_sealed_receipt(
            &self.state_directory,
            SOURCE_STAGING_INTENT_RECEIPT_FILE_NAME,
            &staging_intent.sealed_canonical_bytes()?,
            staging_intent.clone(),
        )?;
        require_worker_receipt_path(
            layout,
            capsule,
            &persisted_intent._receipt_file,
            SOURCE_STAGING_INTENT_RECEIPT_FILE_NAME,
        )?;
        let intent_record =
            authorize_source_staging_intent(capsule, &self.records, handoff, &staging_intent)?;
        self.append_journal_record(capsule, intent_record)?;
        self.recovery_receipt_files
            .push(persisted_intent._receipt_file);
        self.recovery_receipts.staging_intent = Some(staging_intent.clone());

        let stage_relative_name = format!("stage.{}", hex_lower(&capsule.digest()?));
        let (stage_directory, created) = open_or_create_relative_directory(
            &self.state_directory,
            &stage_relative_name,
            STATE_GENERATION_DIRECTORY_SDDL,
            true,
        )?;
        if !created {
            return Err(AuthorityMaintenanceError(
                "authority_worker_staging_path_reused",
            ));
        }
        verify_protected_directory(&stage_directory, STATE_GENERATION_DIRECTORY_SDDL)?;
        require_expected_leaf_path(
            &stage_directory,
            &layout
                .maintenance_worker_source_stage_root(&capsule.digest()?)
                .map_err(|_| AuthorityMaintenanceError("authority_worker_path_invalid"))?,
        )?;
        flush_handle(
            &self.state_directory,
            "authority_worker_directory_parent_flush_failed",
        )?;
        let stage_identity = file_identity(&stage_directory)?;
        let state_identity = file_identity(&self.state_directory)?;
        if stage_identity.volume_serial != state_identity.volume_serial {
            return Err(AuthorityMaintenanceError(
                "authority_worker_source_stage_volume_mismatch",
            ));
        }

        let [service_source, controller_source, install_helper_source, lifecycle_driver_source, bridge_launcher_source, runtime_source_manifest_source] =
            source_handles;
        let (service_file, service_binding) = copy_durable_source_to_relative_file(
            &stage_directory,
            capsule,
            StagedPayloadKind::Service,
            &service_source,
        )?;
        let (controller_file, controller_binding) = copy_durable_source_to_relative_file(
            &stage_directory,
            capsule,
            StagedPayloadKind::Controller,
            &controller_source,
        )?;
        let (install_helper_file, install_helper_binding) = copy_durable_source_to_relative_file(
            &stage_directory,
            capsule,
            StagedPayloadKind::InstallHelper,
            &install_helper_source,
        )?;
        let (lifecycle_driver_file, lifecycle_driver_binding) =
            copy_durable_source_to_relative_file(
                &stage_directory,
                capsule,
                StagedPayloadKind::LifecycleDriver,
                &lifecycle_driver_source,
            )?;
        let (bridge_launcher_file, bridge_launcher_binding) = copy_durable_source_to_relative_file(
            &stage_directory,
            capsule,
            StagedPayloadKind::BridgeLauncher,
            &bridge_launcher_source,
        )?;
        let (runtime_source_manifest_file, runtime_source_manifest_binding) =
            copy_durable_source_to_relative_file(
                &stage_directory,
                capsule,
                StagedPayloadKind::RuntimeSourceManifest,
                &runtime_source_manifest_source,
            )?;
        for (kind, file) in STAGED_PAYLOAD_KINDS.iter().zip([
            &service_file,
            &controller_file,
            &install_helper_file,
            &lifecycle_driver_file,
            &bridge_launcher_file,
            &runtime_source_manifest_file,
        ]) {
            require_expected_leaf_path(
                file,
                &layout
                    .maintenance_worker_source_stage_root(&capsule.digest()?)
                    .map_err(|_| AuthorityMaintenanceError("authority_worker_path_invalid"))?
                    .join(kind.staging_relative_name(capsule)),
            )?;
        }

        let identity_ledger = WorkerSourceIdentityLedger::from_observed(
            capsule,
            worker_started,
            handoff,
            stage_identity.volume_serial,
            stage_identity.file_id,
            service_binding,
            controller_binding,
            install_helper_binding,
            lifecycle_driver_binding,
            bridge_launcher_binding,
            runtime_source_manifest_binding,
        )?;
        let identity_ledger_file = create_relative_file_exact(
            &stage_directory,
            SOURCE_IDENTITY_LEDGER_FILE_NAME,
            STATE_FILE_SDDL,
            &identity_ledger.canonical_bytes(capsule, worker_started, handoff)?,
            FILE_SHARE_READ,
        )?;
        require_expected_leaf_path(
            &identity_ledger_file,
            &layout
                .maintenance_worker_source_identity_ledger_file(&capsule.digest()?)
                .map_err(|_| AuthorityMaintenanceError("authority_worker_path_invalid"))?,
        )?;
        flush_handle(
            &stage_directory,
            "authority_worker_source_stage_directory_flush_failed",
        )?;

        let staging = DurableSourceStagingReceipt::from_observed(
            capsule,
            worker_started,
            handoff,
            &identity_ledger,
        )?;
        staging.validate_identity_ledger(capsule, worker_started, handoff, &identity_ledger)?;
        let staging_receipt_file = create_relative_file_exact(
            &self.state_directory,
            SOURCE_STAGING_RECEIPT_FILE_NAME,
            STATE_FILE_SDDL,
            &staging.canonical_bytes(capsule, worker_started, handoff)?,
            FILE_SHARE_READ,
        )?;
        require_expected_leaf_path(
            &staging_receipt_file,
            &layout
                .maintenance_worker_source_staging_receipt_file(&capsule.digest()?)
                .map_err(|_| AuthorityMaintenanceError("authority_worker_path_invalid"))?,
        )?;
        let source_bound = authorize_source_handles_bound_after_intent(
            capsule,
            &self.records,
            handoff,
            &staging_intent,
            &staging,
        )?;
        self.append_journal_record(capsule, source_bound)?;

        self.recovery_receipt_files.push(staging_receipt_file);
        self.recovery_receipts.staging = Some(staging.clone());
        self.source_store = Some(NativeWorkerSourceStore {
            _directory: stage_directory,
            _source_handles: Some([
                service_source,
                controller_source,
                install_helper_source,
                lifecycle_driver_source,
                bridge_launcher_source,
                runtime_source_manifest_source,
            ]),
            _staged_files: [
                service_file,
                controller_file,
                install_helper_file,
                lifecycle_driver_file,
                bridge_launcher_file,
                runtime_source_manifest_file,
            ],
            _identity_ledger_file: identity_ledger_file,
            identity_ledger,
        });
        Ok(staging)
    }

    pub(super) fn contain_partial_native_worker_staging(
        &mut self,
        layout: &AuthorityLayout,
        capsule: &MaintenanceWorkerCapsule,
    ) -> Result<WorkerPartialStagingCleanupReceipt, AuthorityMaintenanceError> {
        let intent = self
            .recovery_receipts
            .staging_intent
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_source_staging_intent_not_persisted",
            ))?
            .clone();
        let last_phase = self
            .records
            .last()
            .map(MaintenanceWorkerJournalRecord::phase);
        if self.recovery_receipts.partial_staging_cleanup.is_some()
            || !matches!(
                last_phase,
                Some(MaintenanceWorkerPhase::SourceStagingIntent)
                    | Some(MaintenanceWorkerPhase::SourceHandlesBound)
            )
            || (last_phase == Some(MaintenanceWorkerPhase::SourceStagingIntent)
                && self.recovery_receipts.staging.is_some())
            || (last_phase == Some(MaintenanceWorkerPhase::SourceHandlesBound)
                && self.recovery_receipts.staging.is_none())
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_partial_staging_cleanup_phase_invalid",
            ));
        }

        let stage_relative_name = format!("stage.{}", hex_lower(&capsule.digest()?));
        let stage_path = layout
            .maintenance_worker_source_stage_root(&capsule.digest()?)
            .map_err(|_| AuthorityMaintenanceError("authority_worker_path_invalid"))?;
        let mut stage_directory = None;
        run_partial_staging_cleanup_steps(|phase| match phase {
            NativePartialStagingCleanupPhase::CloseSourceHandles => {
                drop(self.source_store.take());
                Ok(())
            }
            NativePartialStagingCleanupPhase::OpenStageDirectory => {
                stage_directory = open_relative_optional_for_delete(
                    &self.state_directory,
                    &stage_relative_name,
                    true,
                )?;
                if let Some(stage_directory) = stage_directory.as_ref() {
                    verify_protected_directory(stage_directory, STATE_GENERATION_DIRECTORY_SDDL)?;
                    require_expected_leaf_path(stage_directory, &stage_path)?;
                }
                Ok(())
            }
            NativePartialStagingCleanupPhase::DeleteServicePayload
            | NativePartialStagingCleanupPhase::DeleteControllerPayload
            | NativePartialStagingCleanupPhase::DeleteInstallHelperPayload
            | NativePartialStagingCleanupPhase::DeleteLifecycleDriverPayload
            | NativePartialStagingCleanupPhase::DeleteBridgeLauncherPayload
            | NativePartialStagingCleanupPhase::DeleteRuntimeSourceManifestPayload => {
                let Some(stage_directory) = stage_directory.as_ref() else {
                    return Ok(());
                };
                let kind = match phase {
                    NativePartialStagingCleanupPhase::DeleteServicePayload => {
                        StagedPayloadKind::Service
                    }
                    NativePartialStagingCleanupPhase::DeleteControllerPayload => {
                        StagedPayloadKind::Controller
                    }
                    NativePartialStagingCleanupPhase::DeleteInstallHelperPayload => {
                        StagedPayloadKind::InstallHelper
                    }
                    NativePartialStagingCleanupPhase::DeleteLifecycleDriverPayload => {
                        StagedPayloadKind::LifecycleDriver
                    }
                    NativePartialStagingCleanupPhase::DeleteBridgeLauncherPayload => {
                        StagedPayloadKind::BridgeLauncher
                    }
                    NativePartialStagingCleanupPhase::DeleteRuntimeSourceManifestPayload => {
                        StagedPayloadKind::RuntimeSourceManifest
                    }
                    _ => unreachable!("matched payload cleanup phase"),
                };
                let name = kind.staging_relative_name(capsule);
                remove_known_partial_stage_entry(stage_directory, &name, &stage_path.join(&name))
            }
            NativePartialStagingCleanupPhase::DeleteIdentityLedger => {
                let Some(stage_directory) = stage_directory.as_ref() else {
                    return Ok(());
                };
                remove_known_partial_stage_entry(
                    stage_directory,
                    SOURCE_IDENTITY_LEDGER_FILE_NAME,
                    &stage_path.join(SOURCE_IDENTITY_LEDGER_FILE_NAME),
                )
            }
            NativePartialStagingCleanupPhase::DeleteStageDirectory => {
                if let Some(directory) = stage_directory.take() {
                    mark_delete_on_close(&directory)?;
                    drop(directory);
                }
                Ok(())
            }
            NativePartialStagingCleanupPhase::VerifyStageAbsent => {
                if open_relative_optional_for_delete(
                    &self.state_directory,
                    &stage_relative_name,
                    true,
                )?
                .is_some()
                {
                    return Err(AuthorityMaintenanceError(
                        "authority_worker_partial_staging_residue",
                    ));
                }
                Ok(())
            }
            NativePartialStagingCleanupPhase::FlushStateParent => flush_handle(
                &self.state_directory,
                "authority_worker_partial_staging_parent_flush_failed",
            ),
        })?;
        let cleanup_readback_sha256 = partial_staging_absence_readback(capsule, &intent)?;
        let cleanup = WorkerPartialStagingCleanupReceipt::from_observed(
            capsule,
            &intent,
            cleanup_readback_sha256,
        )?;
        let receipt_file = create_relative_file_exact(
            &self.state_directory,
            PARTIAL_STAGING_CLEANUP_RECEIPT_FILE_NAME,
            STATE_FILE_SDDL,
            &cleanup.sealed_canonical_bytes()?,
            FILE_SHARE_READ,
        )?;
        require_worker_receipt_path(
            layout,
            capsule,
            &receipt_file,
            PARTIAL_STAGING_CLEANUP_RECEIPT_FILE_NAME,
        )?;
        let record =
            authorize_partial_staging_contained(capsule, &self.records, &intent, &cleanup)?;
        self.append_journal_record(capsule, record)?;
        self.recovery_receipt_files.push(receipt_file);
        self.recovery_receipts.partial_staging_cleanup = Some(cleanup.clone());
        Ok(cleanup)
    }

    pub(super) fn resolve_native_worker_source_staging_after_terminal(
        &mut self,
        layout: &AuthorityLayout,
        capsule: &MaintenanceWorkerCapsule,
        disposition: super::worker::WorkerStagingTerminalDisposition,
        adopted_generation_readback_sha256: Option<[u8; 32]>,
        containment_seal_sha256: Option<[u8; 32]>,
    ) -> Result<WorkerStagingCleanupReceipt, AuthorityMaintenanceError> {
        let staging = self
            .recovery_receipts
            .staging
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_staging_receipt_not_persisted",
            ))?
            .clone();
        let terminal = self
            .records
            .last()
            .filter(|record| record.phase().is_transaction_terminal())
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_transaction_terminal_missing",
            ))?
            .clone();
        if matches!(
            disposition,
            super::worker::WorkerStagingTerminalDisposition::SealedContained
        ) {
            return Err(AuthorityMaintenanceError(
                "authority_worker_source_stage_resolution_invalid",
            ));
        }

        let stage_relative_name = format!("stage.{}", hex_lower(&capsule.digest()?));
        let stage_path = layout
            .maintenance_worker_source_stage_root(&capsule.digest()?)
            .map_err(|_| AuthorityMaintenanceError("authority_worker_path_invalid"))?;
        let mut stage_directory = None;
        run_partial_staging_cleanup_steps(|phase| match phase {
            NativePartialStagingCleanupPhase::CloseSourceHandles => {
                drop(self.source_store.take());
                Ok(())
            }
            NativePartialStagingCleanupPhase::OpenStageDirectory => {
                stage_directory = open_relative_optional_for_delete(
                    &self.state_directory,
                    &stage_relative_name,
                    true,
                )?;
                if let Some(stage_directory) = stage_directory.as_ref() {
                    verify_protected_directory(stage_directory, STATE_GENERATION_DIRECTORY_SDDL)?;
                    require_expected_leaf_path(stage_directory, &stage_path)?;
                }
                Ok(())
            }
            NativePartialStagingCleanupPhase::DeleteServicePayload
            | NativePartialStagingCleanupPhase::DeleteControllerPayload
            | NativePartialStagingCleanupPhase::DeleteInstallHelperPayload
            | NativePartialStagingCleanupPhase::DeleteLifecycleDriverPayload
            | NativePartialStagingCleanupPhase::DeleteBridgeLauncherPayload
            | NativePartialStagingCleanupPhase::DeleteRuntimeSourceManifestPayload => {
                let Some(stage_directory) = stage_directory.as_ref() else {
                    return Ok(());
                };
                let kind = match phase {
                    NativePartialStagingCleanupPhase::DeleteServicePayload => {
                        StagedPayloadKind::Service
                    }
                    NativePartialStagingCleanupPhase::DeleteControllerPayload => {
                        StagedPayloadKind::Controller
                    }
                    NativePartialStagingCleanupPhase::DeleteInstallHelperPayload => {
                        StagedPayloadKind::InstallHelper
                    }
                    NativePartialStagingCleanupPhase::DeleteLifecycleDriverPayload => {
                        StagedPayloadKind::LifecycleDriver
                    }
                    NativePartialStagingCleanupPhase::DeleteBridgeLauncherPayload => {
                        StagedPayloadKind::BridgeLauncher
                    }
                    NativePartialStagingCleanupPhase::DeleteRuntimeSourceManifestPayload => {
                        StagedPayloadKind::RuntimeSourceManifest
                    }
                    _ => unreachable!("matched payload cleanup phase"),
                };
                let name = kind.staging_relative_name(capsule);
                remove_known_partial_stage_entry(stage_directory, &name, &stage_path.join(&name))
            }
            NativePartialStagingCleanupPhase::DeleteIdentityLedger => {
                let Some(stage_directory) = stage_directory.as_ref() else {
                    return Ok(());
                };
                remove_known_partial_stage_entry(
                    stage_directory,
                    SOURCE_IDENTITY_LEDGER_FILE_NAME,
                    &stage_path.join(SOURCE_IDENTITY_LEDGER_FILE_NAME),
                )
            }
            NativePartialStagingCleanupPhase::DeleteStageDirectory => {
                if let Some(directory) = stage_directory.take() {
                    mark_delete_on_close(&directory)?;
                    drop(directory);
                }
                Ok(())
            }
            NativePartialStagingCleanupPhase::VerifyStageAbsent => {
                if open_relative_optional_for_delete(
                    &self.state_directory,
                    &stage_relative_name,
                    true,
                )?
                .is_some()
                {
                    return Err(AuthorityMaintenanceError(
                        "authority_worker_source_stage_resolution_residue",
                    ));
                }
                Ok(())
            }
            NativePartialStagingCleanupPhase::FlushStateParent => flush_handle(
                &self.state_directory,
                "authority_worker_source_stage_resolution_flush_failed",
            ),
        })?;
        let cleanup_readback_sha256 =
            terminal_staging_absence_readback(capsule, &staging, &terminal, disposition)?;
        let cleanup = WorkerStagingCleanupReceipt::from_observed(
            capsule,
            &staging,
            &terminal,
            disposition,
            true,
            adopted_generation_readback_sha256,
            containment_seal_sha256,
            cleanup_readback_sha256,
        )?;
        self.record_source_stage_resolved(layout, capsule, cleanup.clone())?;
        Ok(cleanup)
    }

    pub(super) fn authorize_native_transaction_start(
        &mut self,
        layout: &AuthorityLayout,
        capsule: &MaintenanceWorkerCapsule,
        nonce: NativeNonceConsumptionLease,
        now_unix_millis: u64,
    ) -> Result<TransactionStartedReceipt, AuthorityMaintenanceError> {
        let handoff = self
            .recovery_receipts
            .handoff
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_missing",
            ))?
            .clone();
        let staging = self
            .recovery_receipts
            .staging
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_missing",
            ))?
            .clone();
        let (record, receipt) = authorize_transaction_start(
            capsule,
            &self.records,
            &handoff,
            &staging,
            nonce.receipt(),
            now_unix_millis,
        )?;
        let persisted = persist_or_reopen_sealed_receipt(
            &self.state_directory,
            TRANSACTION_STARTED_RECEIPT_FILE_NAME,
            &receipt.sealed_canonical_bytes()?,
            receipt.clone(),
        )?;
        require_worker_receipt_path(
            layout,
            capsule,
            &persisted._receipt_file,
            TRANSACTION_STARTED_RECEIPT_FILE_NAME,
        )?;
        self.append_journal_record(capsule, record)?;
        self.recovery_receipt_files.push(persisted._receipt_file);
        self.recovery_receipts.nonce_consumption = Some(nonce.receipt().clone());
        self.recovery_receipts.transaction_started = Some(receipt.clone());
        self.nonce_consumption_lease = Some(nonce);
        Ok(receipt)
    }

    pub(super) fn prepare_candidate_activation_credential(
        &self,
        layout: &AuthorityLayout,
        capsule: &MaintenanceWorkerCapsule,
        prepared: CandidateCredentialRecord,
    ) -> Result<NativeCandidateCredentialLease, AuthorityMaintenanceError> {
        self.validate_prepared_candidate_binding(capsule, &prepared)?;
        let final_name = candidate_credential_file_name(&capsule.transaction_sha256()?)
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        let prepared_name = format!("{final_name}{CANDIDATE_PREPARED_SUFFIX}");
        persist_candidate_credential_create_new(
            layout,
            &prepared_name,
            CandidateCredentialPhase::Prepared,
            prepared,
        )
    }

    pub(super) fn arm_candidate_activation_credential(
        &mut self,
        layout: &AuthorityLayout,
        capsule: &MaintenanceWorkerCapsule,
        prepared: NativeCandidateCredentialLease,
        candidate_service: CandidateProcessEvidence,
    ) -> Result<CandidateCredentialRecord, AuthorityMaintenanceError> {
        self.validate_prepared_candidate_binding(capsule, prepared.record())?;
        verify_candidate_credential_lease(
            layout,
            &format!(
                "{}{}",
                candidate_credential_file_name(&capsule.transaction_sha256()?)
                    .map_err(|error| AuthorityMaintenanceError(error.code()))?,
                CANDIDATE_PREPARED_SUFFIX
            ),
            CandidateCredentialPhase::Prepared,
            &prepared,
        )?;
        candidate_service
            .validate()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        let transaction_started = self
            .recovery_receipts
            .transaction_started
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_transaction_receipt_not_persisted",
            ))?
            .clone();
        let worker_started = self
            .recovery_receipts
            .worker_started
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_started_receipt_not_persisted",
            ))?
            .clone();
        let maintenance_worker = *prepared
            .record()
            .binding()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?
            .issuer()
            .maintenance_worker();
        let armed_receipt = CandidateCredentialArmedReceipt::from_observed(
            capsule,
            &transaction_started,
            &worker_started,
            prepared.record(),
            prepared.file_identity.volume_serial,
            prepared.file_identity.file_id,
            maintenance_worker,
            candidate_service,
        )?;
        let persisted_receipt = persist_sealed_receipt(
            &self.state_directory,
            CANDIDATE_CREDENTIAL_ARMED_RECEIPT_FILE_NAME,
            &armed_receipt.sealed_canonical_bytes()?,
            armed_receipt.clone(),
        )
        .map_err(|_| {
            AuthorityMaintenanceError("authority_candidate_credential_persistence_indeterminate")
        })?;
        require_worker_receipt_path(
            layout,
            capsule,
            &persisted_receipt._receipt_file,
            CANDIDATE_CREDENTIAL_ARMED_RECEIPT_FILE_NAME,
        )?;

        let armed = prepared
            .record()
            .arm_with_receipt(armed_receipt.digest()?, candidate_service)
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        let final_name = candidate_credential_file_name(&capsule.transaction_sha256()?)
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        let armed_lease = persist_candidate_credential_create_new(
            layout,
            &final_name,
            CandidateCredentialPhase::Armed,
            armed.clone(),
        )?;
        verify_candidate_credential_lease(
            layout,
            &final_name,
            CandidateCredentialPhase::Armed,
            &armed_lease,
        )?;
        if armed_lease
            .record()
            .armed_receipt_sha256()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?
            != armed_receipt.digest()?
            || armed_lease.record().candidate_service() != Some(&candidate_service)
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_credential_persistence_indeterminate",
            ));
        }

        let record = authorize_candidate_credential_armed(
            capsule,
            &self.records,
            &transaction_started,
            &worker_started,
            prepared.record(),
            &armed_receipt,
        )?;
        self.append_journal_record(capsule, record)?;
        self.recovery_receipt_files
            .push(persisted_receipt._receipt_file);
        self.recovery_receipts.candidate_credential_armed = Some(armed_receipt);
        self.candidate_prepared = Some(prepared);
        self.candidate_armed = Some(armed_lease);
        Ok(armed)
    }

    fn validate_prepared_candidate_binding(
        &self,
        capsule: &MaintenanceWorkerCapsule,
        prepared: &CandidateCredentialRecord,
    ) -> Result<(), AuthorityMaintenanceError> {
        let transaction_started = self.recovery_receipts.transaction_started.as_ref().ok_or(
            AuthorityMaintenanceError("authority_worker_transaction_receipt_not_persisted"),
        )?;
        let worker_started =
            self.recovery_receipts
                .worker_started
                .as_ref()
                .ok_or(AuthorityMaintenanceError(
                    "authority_worker_started_receipt_not_persisted",
                ))?;
        let nonce = self
            .nonce_consumption_lease
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_nonce_consumption_not_held",
            ))?;
        if self
            .records
            .last()
            .map(MaintenanceWorkerJournalRecord::phase)
            != Some(MaintenanceWorkerPhase::TransactionStarted)
            || self.candidate_prepared.is_some()
            || self.candidate_armed.is_some()
            || self.recovery_receipts.candidate_credential_armed.is_some()
            || prepared.phase() != CandidateCredentialPhase::Prepared
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_prepared_phase_invalid",
            ));
        }
        let binding = prepared
            .binding()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        let issuer = binding.issuer();
        let (
            nonce_consumption_receipt_sha256,
            nonce_consumption_full_readback_sha256,
            nonce_consumption_file_sha256,
            nonce_consumption_file_volume_serial,
            nonce_consumption_file_id,
        ) = nonce.durable_binding(capsule)?;
        if *binding.plan_sha256() != capsule.plan_sha256()?
            || *binding.generation() != capsule.generation()?
            || *binding.transaction_sha256() != capsule.transaction_sha256()?
            || *issuer.capsule_sha256() != capsule.digest()?
            || *issuer.transaction_started_receipt_sha256() != transaction_started.digest()?
            || *issuer.worker_started_receipt_sha256() != worker_started.digest()?
            || !worker_started.matches_candidate_process_evidence(issuer.maintenance_worker())?
            || *issuer.nonce_consumption_receipt_sha256() != nonce_consumption_receipt_sha256
            || *issuer.nonce_consumption_full_readback_sha256()
                != nonce_consumption_full_readback_sha256
            || *issuer.nonce_consumption_file_sha256() != nonce_consumption_file_sha256
            || issuer.nonce_consumption_file_volume_serial() != nonce_consumption_file_volume_serial
            || *issuer.nonce_consumption_file_id() != nonce_consumption_file_id
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_prepared_binding_mismatch",
            ));
        }
        Ok(())
    }
}

pub(super) fn consume_native_worker_nonce(
    layout: &AuthorityLayout,
    capsule: &MaintenanceWorkerCapsule,
    now_unix_millis: u64,
) -> Result<NativeNonceConsumptionLease, AuthorityMaintenanceError> {
    capsule.validate_consent_at(now_unix_millis)?;
    let transaction_nonce_sha256 = capsule.transaction_nonce_sha256()?;
    let (mut state_chain, state_version_root) = open_protected_chain(
        layout.state_base(),
        &[],
        "v1",
        STATE_DIRECTORY_SDDL,
        STATE_DIRECTORY_SDDL,
    )?;
    require_expected_leaf_path(&state_version_root, layout.state_root())?;
    let (nonce_root, created) = nt_open_relative(
        &state_version_root,
        "worker-nonce-receipts",
        WORKER_NONCE_DIRECTORY_SDDL,
        true,
        FILE_OPEN,
        DIRECTORY_CREATE_FILE_ACCESS | ACCESS_SYSTEM_SECURITY,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
    )?;
    if created {
        return Err(AuthorityMaintenanceError(
            "authority_worker_nonce_root_was_not_preprovisioned",
        ));
    }
    verify_protected_directory(&nonce_root, WORKER_NONCE_DIRECTORY_SDDL)?;
    require_expected_leaf_path(&nonce_root, &layout.worker_nonce_root())?;
    let relative_name = format!(
        "nonce.{}.consumed.json",
        hex_lower(&transaction_nonce_sha256)
    );
    let (receipt_file, receipt_created) = nt_open_relative(
        &nonce_root,
        &relative_name,
        WORKER_NONCE_FILE_SDDL,
        false,
        FILE_CREATE,
        FILE_WRITE_ONCE_ACCESS | ACCESS_SYSTEM_SECURITY,
        FILE_SHARE_READ,
    )?;
    if !receipt_created {
        return Err(AuthorityMaintenanceError(
            "authority_worker_nonce_already_consumed",
        ));
    }
    verify_protected_file(&receipt_file, WORKER_NONCE_FILE_SDDL)?;
    require_expected_leaf_path(
        &receipt_file,
        &layout
            .worker_nonce_receipt_file(&transaction_nonce_sha256)
            .map_err(|_| AuthorityMaintenanceError("authority_worker_nonce_path_invalid"))?,
    )?;
    let nonce_root_identity = file_identity(&nonce_root)?;
    let receipt_identity_before = file_identity(&receipt_file)?;
    if receipt_identity_before.byte_length != 0
        || receipt_identity_before.link_count != 1
        || receipt_identity_before.attributes
            & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)
            != 0
        || nonce_root_identity.volume_serial != receipt_identity_before.volume_serial
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_nonce_file_identity_invalid",
        ));
    }
    let receipt = WorkerNonceConsumptionReceipt::from_observed(
        capsule,
        now_unix_millis,
        nonce_root_identity.volume_serial,
        nonce_root_identity.file_id,
        receipt_identity_before.volume_serial,
        receipt_identity_before.file_id,
    )?;
    if receipt.relative_name() != relative_name {
        return Err(AuthorityMaintenanceError(
            "authority_worker_nonce_path_invalid",
        ));
    }
    let bytes = receipt.canonical_bytes(capsule)?;
    write_held_file_exact(&receipt_file, &bytes)?;
    flush_handle(&receipt_file, "authority_worker_nonce_receipt_flush_failed")?;
    flush_handle(&nonce_root, "authority_worker_nonce_root_flush_failed")?;
    let receipt_identity_after = file_identity(&receipt_file)?;
    if receipt_identity_after.volume_serial != receipt_identity_before.volume_serial
        || receipt_identity_after.file_id != receipt_identity_before.file_id
        || receipt_identity_after.link_count != 1
        || receipt_identity_after.byte_length != bytes.len() as u64
        || read_held_file_bounded(&receipt_file, MAX_PROTECTED_RECEIPT_BYTES)? != bytes
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_nonce_receipt_readback_mismatch",
        ));
    }
    state_chain.push(state_version_root);
    Ok(NativeNonceConsumptionLease {
        _state_chain: state_chain,
        _nonce_root: nonce_root,
        _receipt_file: receipt_file,
        receipt,
        nonce_root_volume_serial: nonce_root_identity.volume_serial,
        nonce_root_file_id: nonce_root_identity.file_id,
        file_identity: receipt_identity_after,
        bytes_sha256: Sha256::digest(&bytes).into(),
    })
}

fn open_native_worker_nonce(
    layout: &AuthorityLayout,
    capsule: &MaintenanceWorkerCapsule,
) -> Result<NativeNonceConsumptionLease, AuthorityMaintenanceError> {
    let transaction_nonce_sha256 = capsule.transaction_nonce_sha256()?;
    let (mut state_chain, state_version_root) = open_protected_chain(
        layout.state_base(),
        &[],
        "v1",
        STATE_DIRECTORY_SDDL,
        STATE_DIRECTORY_SDDL,
    )?;
    require_expected_leaf_path(&state_version_root, layout.state_root())?;
    let (nonce_root, created) = nt_open_relative(
        &state_version_root,
        "worker-nonce-receipts",
        WORKER_NONCE_DIRECTORY_SDDL,
        true,
        FILE_OPEN,
        DIRECTORY_READ_ACCESS | ACCESS_SYSTEM_SECURITY,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
    )?;
    if created {
        return Err(AuthorityMaintenanceError(
            "authority_worker_recovery_created_path",
        ));
    }
    verify_protected_directory(&nonce_root, WORKER_NONCE_DIRECTORY_SDDL)?;
    require_expected_leaf_path(&nonce_root, &layout.worker_nonce_root())?;
    let relative_name = format!(
        "nonce.{}.consumed.json",
        hex_lower(&transaction_nonce_sha256)
    );
    let receipt_file = open_relative_file_existing(
        &nonce_root,
        &relative_name,
        WORKER_NONCE_FILE_SDDL,
        FILE_SHARE_READ,
        false,
    )?;
    require_expected_leaf_path(
        &receipt_file,
        &layout
            .worker_nonce_receipt_file(&transaction_nonce_sha256)
            .map_err(|_| AuthorityMaintenanceError("authority_worker_nonce_path_invalid"))?,
    )?;
    let bytes = read_held_file_bounded(&receipt_file, MAX_PROTECTED_RECEIPT_BYTES)?;
    let receipt = WorkerNonceConsumptionReceipt::parse_sealed_canonical(&bytes)?;
    receipt.validate(capsule)?;
    let nonce_root_identity = file_identity(&nonce_root)?;
    let receipt_identity = file_identity(&receipt_file)?;
    verify_exact_file_identity(&receipt_identity, bytes.len() as u64)?;
    state_chain.push(state_version_root);
    Ok(NativeNonceConsumptionLease {
        _state_chain: state_chain,
        _nonce_root: nonce_root,
        _receipt_file: receipt_file,
        receipt,
        nonce_root_volume_serial: nonce_root_identity.volume_serial,
        nonce_root_file_id: nonce_root_identity.file_id,
        file_identity: receipt_identity,
        bytes_sha256: Sha256::digest(&bytes).into(),
    })
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum CandidateConsumptionReadbackScope {
    FullSecurity,
    CandidateAccessible,
}

impl CandidateConsumptionReadbackScope {
    const fn directory_access(self) -> u32 {
        match self {
            Self::FullSecurity => DIRECTORY_READ_ACCESS | ACCESS_SYSTEM_SECURITY,
            Self::CandidateAccessible => DIRECTORY_READ_ACCESS,
        }
    }

    const fn file_access(self) -> u32 {
        match self {
            Self::FullSecurity => FILE_READ_ACCESS | ACCESS_SYSTEM_SECURITY,
            Self::CandidateAccessible => FILE_READ_ACCESS,
        }
    }

    const fn security_information(self) -> u32 {
        match self {
            Self::FullSecurity => SECURITY_INFORMATION,
            Self::CandidateAccessible => CANDIDATE_SECURITY_INFORMATION,
        }
    }
}

pub(super) fn open_candidate_consumption_tombstone(
    layout: &AuthorityLayout,
    credential_sha256: &[u8; 32],
) -> Result<Option<NativeCandidateConsumptionLease>, AuthorityMaintenanceError> {
    open_candidate_consumption_tombstone_with_scope(
        layout,
        credential_sha256,
        CandidateConsumptionReadbackScope::FullSecurity,
    )
}

pub(super) fn open_candidate_consumption_tombstone_for_candidate(
    layout: &AuthorityLayout,
    credential_sha256: &[u8; 32],
) -> Result<Option<NativeCandidateConsumptionLease>, AuthorityMaintenanceError> {
    open_candidate_consumption_tombstone_with_scope(
        layout,
        credential_sha256,
        CandidateConsumptionReadbackScope::CandidateAccessible,
    )
}

fn open_candidate_consumption_tombstone_with_scope(
    layout: &AuthorityLayout,
    credential_sha256: &[u8; 32],
    scope: CandidateConsumptionReadbackScope,
) -> Result<Option<NativeCandidateConsumptionLease>, AuthorityMaintenanceError> {
    let relative_name = candidate_consumption_tombstone_name(credential_sha256)?;
    let indeterminate = |_: AuthorityMaintenanceError| {
        AuthorityMaintenanceError("authority_candidate_consumption_indeterminate_consumed")
    };
    let (mut state_chain, state_version_root) = match scope {
        CandidateConsumptionReadbackScope::FullSecurity => open_protected_chain(
            layout.state_base(),
            &[],
            "v1",
            STATE_DIRECTORY_SDDL,
            STATE_DIRECTORY_SDDL,
        ),
        CandidateConsumptionReadbackScope::CandidateAccessible => {
            open_candidate_consumption_state_chain(layout)
        }
    }
    .map_err(indeterminate)?;
    require_expected_leaf_path(&state_version_root, layout.state_root()).map_err(indeterminate)?;
    let Some(nonce_root) = nt_open_relative_optional(
        &state_version_root,
        "candidate-consumption-tombstones",
        CANDIDATE_CONSUMPTION_DIRECTORY_SDDL,
        true,
        scope.directory_access(),
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
    )
    .map_err(indeterminate)?
    else {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_consumption_root_not_preprovisioned",
        ));
    };
    verify_protected_directory_with_information(
        &nonce_root,
        CANDIDATE_CONSUMPTION_DIRECTORY_SDDL,
        scope.security_information(),
    )
    .map_err(indeterminate)?;
    require_expected_leaf_path(&nonce_root, &layout.candidate_consumption_root())
        .map_err(indeterminate)?;
    let nonce_root_identity = file_identity(&nonce_root).map_err(indeterminate)?;
    let receipt_file = match nt_open_relative_optional(
        &nonce_root,
        &relative_name,
        CANDIDATE_CONSUMPTION_FILE_SDDL,
        false,
        scope.file_access(),
        FILE_SHARE_READ,
    ) {
        Ok(Some(file)) => file,
        Ok(None) => return Ok(None),
        Err(_) => {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_consumption_indeterminate_consumed",
            ))
        }
    };
    (|| {
        verify_protected_file_with_information(
            &receipt_file,
            CANDIDATE_CONSUMPTION_FILE_SDDL,
            scope.security_information(),
        )?;
        require_expected_leaf_path(
            &receipt_file,
            &layout.candidate_consumption_root().join(&relative_name),
        )?;
        let identity = file_identity(&receipt_file)?;
        let bytes = read_held_file_bounded(&receipt_file, MAX_PROTECTED_RECEIPT_BYTES)?;
        validate_candidate_consumption_tombstone(credential_sha256, &bytes)?;
        verify_exact_file_identity(&identity, bytes.len() as u64)?;
        if file_identity(&receipt_file)? != identity
            || file_identity(&nonce_root)? != nonce_root_identity
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_consumption_tombstone_readback_mismatch",
            ));
        }
        let bytes_sha256 = Sha256::digest(&bytes).into();
        state_chain.push(state_version_root);
        Ok(Some(NativeCandidateConsumptionLease {
            _state_chain: state_chain,
            _nonce_root: nonce_root,
            _receipt_file: receipt_file,
            bytes,
            file_identity: identity,
            bytes_sha256,
        }))
    })()
    .map_err(|_| {
        AuthorityMaintenanceError("authority_candidate_consumption_indeterminate_consumed")
    })
}

pub(super) fn create_candidate_consumption_tombstone_for_candidate(
    layout: &AuthorityLayout,
    credential_sha256: &[u8; 32],
    canonical_consumed_record: &[u8],
) -> Result<NativeCandidateConsumptionLease, AuthorityMaintenanceError> {
    let relative_name = candidate_consumption_tombstone_name(credential_sha256)?;
    validate_candidate_consumption_tombstone(credential_sha256, canonical_consumed_record)?;
    let (mut state_chain, state_version_root) = open_candidate_consumption_state_chain(layout)?;
    require_expected_leaf_path(&state_version_root, layout.state_root())?;
    let (nonce_root, created) = nt_open_relative(
        &state_version_root,
        "candidate-consumption-tombstones",
        CANDIDATE_CONSUMPTION_DIRECTORY_SDDL,
        true,
        FILE_OPEN,
        DIRECTORY_CREATE_FILE_ACCESS,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
    )?;
    if created {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_consumption_root_was_not_preprovisioned",
        ));
    }
    verify_candidate_protected_directory(&nonce_root, CANDIDATE_CONSUMPTION_DIRECTORY_SDDL)?;
    require_expected_leaf_path(&nonce_root, &layout.candidate_consumption_root())?;
    let nonce_root_identity = file_identity(&nonce_root)?;
    let receipt_file = create_candidate_consumption_file(&nonce_root, &relative_name)?;
    // Once FILE_CREATE succeeds this artifact is intentionally never removed:
    // any later failure is an indeterminate one-use consumption, not rollback.
    (|| {
        verify_candidate_protected_file(&receipt_file, CANDIDATE_CONSUMPTION_FILE_SDDL)?;
        require_expected_leaf_path(
            &receipt_file,
            &layout.candidate_consumption_root().join(&relative_name),
        )?;
        let identity_before = file_identity(&receipt_file)?;
        if identity_before.byte_length != 0
            || identity_before.link_count != 1
            || identity_before.attributes
                & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)
                != 0
            || identity_before.volume_serial != nonce_root_identity.volume_serial
        {
            return Err(AuthorityMaintenanceError(
                "authority_candidate_consumption_tombstone_identity_invalid",
            ));
        }
        let mut durable_identity = None;
        run_candidate_tombstone_persistence(|phase| match phase {
            NativeCandidateTombstonePersistencePhase::WriteExact => {
                write_held_file_exact(&receipt_file, canonical_consumed_record)
            }
            NativeCandidateTombstonePersistencePhase::FlushFile => flush_handle(
                &receipt_file,
                "authority_candidate_consumption_tombstone_flush_failed",
            ),
            NativeCandidateTombstonePersistencePhase::FlushParent => flush_handle(
                &nonce_root,
                "authority_candidate_consumption_tombstone_parent_flush_failed",
            ),
            NativeCandidateTombstonePersistencePhase::VerifySecurityPathIdentityAndBytes => {
                verify_candidate_protected_file(&receipt_file, CANDIDATE_CONSUMPTION_FILE_SDDL)?;
                require_expected_leaf_path(
                    &receipt_file,
                    &layout.candidate_consumption_root().join(&relative_name),
                )?;
                let identity = file_identity(&receipt_file)?;
                verify_exact_file_identity(&identity, canonical_consumed_record.len() as u64)?;
                if identity.volume_serial != identity_before.volume_serial
                    || identity.file_id != identity_before.file_id
                    || file_identity(&nonce_root)? != nonce_root_identity
                    || read_held_file_bounded(&receipt_file, MAX_PROTECTED_RECEIPT_BYTES)?
                        != canonical_consumed_record
                    || file_identity(&receipt_file)? != identity
                {
                    return Err(AuthorityMaintenanceError(
                        "authority_candidate_consumption_tombstone_readback_mismatch",
                    ));
                }
                durable_identity = Some(identity);
                Ok(())
            }
        })?;
        let identity = durable_identity.ok_or(AuthorityMaintenanceError(
            "authority_candidate_consumption_tombstone_readback_mismatch",
        ))?;
        let bytes = canonical_consumed_record.to_vec();
        let bytes_sha256 = Sha256::digest(&bytes).into();
        state_chain.push(state_version_root);
        Ok(NativeCandidateConsumptionLease {
            _state_chain: state_chain,
            _nonce_root: nonce_root,
            _receipt_file: receipt_file,
            bytes,
            file_identity: identity,
            bytes_sha256,
        })
    })()
    .map_err(|_| {
        AuthorityMaintenanceError("authority_candidate_consumption_indeterminate_consumed")
    })
}

fn candidate_consumption_tombstone_name(
    credential_sha256: &[u8; 32],
) -> Result<String, AuthorityMaintenanceError> {
    if credential_sha256.iter().all(|value| *value == 0) {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_consumption_credential_invalid",
        ));
    }
    Ok(format!(
        "candidate.{}.consumed.json",
        hex_lower(credential_sha256)
    ))
}

fn validate_candidate_consumption_tombstone(
    credential_sha256: &[u8; 32],
    bytes: &[u8],
) -> Result<(), AuthorityMaintenanceError> {
    if bytes.is_empty() || bytes.len() > MAX_PROTECTED_RECEIPT_BYTES {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_consumption_tombstone_size_invalid",
        ));
    }
    let record = CandidateCredentialRecord::parse_canonical(bytes).map_err(|_| {
        AuthorityMaintenanceError("authority_candidate_consumption_tombstone_invalid")
    })?;
    if record.phase() != CandidateCredentialPhase::Consumed
        || record.credential_sha256().map_err(|_| {
            AuthorityMaintenanceError("authority_candidate_consumption_tombstone_invalid")
        })? != *credential_sha256
        || record.canonical_bytes().map_err(|_| {
            AuthorityMaintenanceError("authority_candidate_consumption_tombstone_invalid")
        })? != bytes
    {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_consumption_tombstone_binding_mismatch",
        ));
    }
    Ok(())
}

pub(super) fn stage_native_worker_bootstrap(
    layout: &AuthorityLayout,
    capsule: &MaintenanceWorkerCapsule,
    capsule_bytes: &[u8],
    lease: &VerifiedMaintenanceLease,
) -> Result<NativeWorkerBootstrapStore, AuthorityMaintenanceError> {
    if capsule_bytes != capsule.canonical_bytes()?
        || capsule_bytes.is_empty()
        || capsule_bytes.len() > MAX_PROTECTED_RECEIPT_BYTES
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_capsule_staging_invalid",
        ));
    }
    let capsule_sha256 = capsule.digest()?;
    let launch = MaintenanceWorkerLaunchContract::new(layout, capsule)?;
    let source_handles = lease.native_source_handles()?;
    validate_source_handle(
        source_handles[2],
        capsule.payload_source_expectation(StagedPayloadKind::InstallHelper)?,
    )?;

    let (state_chain, state_directory) = create_worker_state_chain(layout, &capsule_sha256)?;
    let intent = WorkerBootstrapIntentReceipt::new(capsule, &launch)?;
    let intent_file = create_relative_file_exact(
        &state_directory,
        INTENT_FILE_NAME,
        STATE_FILE_SDDL,
        &intent.canonical_bytes(capsule, &launch)?,
        FILE_SHARE_READ,
    )?;
    let first = MaintenanceWorkerJournalRecord::first_intent(capsule, &launch, &intent)?;
    let mut records = vec![first];
    let initial_journal = encode_worker_journal(capsule_sha256, &records)?;
    let journal_file = create_relative_file_exact(
        &state_directory,
        JOURNAL_FILE_NAME,
        STATE_FILE_SDDL,
        &initial_journal,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
    )?;
    flush_handle(
        &state_directory,
        "authority_worker_journal_parent_flush_failed",
    )?;

    let (binary_chain, binary_directory) = create_worker_binary_chain(layout, &capsule_sha256)?;
    let (worker_file, worker_binding) = copy_source_to_relative_file(
        &binary_directory,
        WORKER_EXECUTABLE_NAME,
        BINARY_FILE_SDDL,
        "install-helper",
        source_handles[2],
        capsule.payload_source_expectation(StagedPayloadKind::InstallHelper)?,
    )?;
    let capsule_descriptor = AuthorityPayloadDigest::new(
        Sha256::digest(capsule_bytes).into(),
        capsule_bytes.len() as u64,
    )?;
    let (capsule_file, capsule_binding) = create_bytes_relative_file(
        &state_directory,
        CAPSULE_FILE_NAME,
        STATE_FILE_SDDL,
        "capsule",
        capsule_bytes,
        capsule_descriptor,
    )?;
    let binary_identity = file_identity(&binary_directory)?;
    let state_identity = file_identity(&state_directory)?;
    let bootstrap = WorkerBootstrapStagingReceipt::from_observed(
        capsule,
        &launch,
        binary_identity.volume_serial,
        binary_identity.file_id,
        state_identity.volume_serial,
        state_identity.file_id,
        worker_binding,
        capsule_binding,
    )?;
    let bootstrap_receipt_file = create_relative_file_exact(
        &state_directory,
        BOOTSTRAP_RECEIPT_FILE_NAME,
        STATE_FILE_SDDL,
        &bootstrap.canonical_bytes(capsule, &launch)?,
        FILE_SHARE_READ,
    )?;
    let capsule_staged = authorize_capsule_staged(capsule, &launch, &records, &intent, &bootstrap)?;
    records.push(capsule_staged);
    let staged_journal = encode_worker_journal(capsule_sha256, &records)?;
    let append = encode_worker_journal_append(
        capsule_sha256,
        &records[..records.len() - 1],
        records.last().ok_or(AuthorityMaintenanceError(
            "authority_worker_journal_missing",
        ))?,
    )?;
    append_held_file_exact(&journal_file, initial_journal.len() as u64, &append)?;
    flush_handle(&journal_file, "authority_worker_journal_flush_failed")?;
    flush_handle(
        &state_directory,
        "authority_worker_journal_parent_flush_failed",
    )?;
    let journal_readback = read_held_file_bounded(&journal_file, MAX_PROTECTED_RECEIPT_BYTES)?;
    if journal_readback != staged_journal
        || parse_worker_journal_recovery(&journal_readback, capsule_sha256)?.records() != records
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_journal_readback_mismatch",
        ));
    }

    Ok(NativeWorkerBootstrapStore {
        _binary_chain: binary_chain,
        _state_chain: state_chain,
        binary_directory,
        state_directory,
        _worker_file: worker_file,
        _capsule_file: capsule_file,
        _intent_file: intent_file,
        _bootstrap_receipt_file: bootstrap_receipt_file,
        recovery_receipt_files: Vec::new(),
        nonce_consumption_lease: None,
        journal_file,
        launch,
        intent,
        bootstrap,
        recovery_receipts: NativeWorkerRecoveryReceipts::default(),
        candidate_prepared: None,
        candidate_armed: None,
        source_store: None,
        records,
        journal_torn_tail: false,
        recovery_requires_containment: false,
    })
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum NativeWorkerOpenPurpose {
    FirstInvocation,
    Recovery,
}

pub(super) fn open_native_worker_bootstrap(
    layout: &AuthorityLayout,
    expected_capsule_sha256: [u8; 32],
) -> Result<(MaintenanceWorkerCapsule, NativeWorkerBootstrapStore), AuthorityMaintenanceError> {
    open_native_worker_bootstrap_for_purpose(
        layout,
        expected_capsule_sha256,
        NativeWorkerOpenPurpose::Recovery,
    )
}

pub(super) fn open_candidate_activation_receipt_binding(
    layout: &AuthorityLayout,
    expected_capsule_sha256: [u8; 32],
    expected_plan_sha256: [u8; 32],
    expected_generation_sha256: [u8; 32],
    expected_transaction_sha256: [u8; 32],
) -> Result<NativeCandidateActivationReceiptBinding, AuthorityMaintenanceError> {
    if [
        expected_capsule_sha256,
        expected_plan_sha256,
        expected_generation_sha256,
        expected_transaction_sha256,
    ]
    .iter()
    .any(|digest| digest.iter().all(|value| *value == 0))
    {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_activation_receipt_binding_invalid",
        ));
    }
    let (capsule, store) = open_native_worker_bootstrap(layout, expected_capsule_sha256)?;
    if capsule.plan_sha256()? != expected_plan_sha256
        || capsule.generation()? != expected_generation_sha256
        || capsule.transaction_sha256()? != expected_transaction_sha256
    {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_activation_receipt_binding_mismatch",
        ));
    }
    let binding = store.candidate_activation_receipt_binding(&capsule)?;
    if binding.capsule_sha256 != expected_capsule_sha256
        || binding.plan_sha256 != expected_plan_sha256
        || binding.generation_sha256 != expected_generation_sha256
        || binding.transaction_sha256 != expected_transaction_sha256
    {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_activation_receipt_binding_mismatch",
        ));
    }
    Ok(binding)
}

pub(super) fn open_native_worker_bootstrap_for_first_invocation(
    layout: &AuthorityLayout,
    expected_capsule_sha256: [u8; 32],
) -> Result<(MaintenanceWorkerCapsule, NativeWorkerBootstrapStore), AuthorityMaintenanceError> {
    open_native_worker_bootstrap_for_purpose(
        layout,
        expected_capsule_sha256,
        NativeWorkerOpenPurpose::FirstInvocation,
    )
}

pub(super) fn read_native_worker_capsule_for_connection(
    layout: &AuthorityLayout,
    expected_capsule_sha256: [u8; 32],
) -> Result<MaintenanceWorkerCapsule, AuthorityMaintenanceError> {
    if expected_capsule_sha256.iter().all(|value| *value == 0) {
        return Err(AuthorityMaintenanceError(
            "authority_worker_capsule_digest_invalid",
        ));
    }
    let (_state_chain, state_directory) =
        open_worker_state_chain(layout, &expected_capsule_sha256)?;
    let capsule_file = open_relative_file_existing(
        &state_directory,
        CAPSULE_FILE_NAME,
        STATE_FILE_SDDL,
        FILE_SHARE_READ,
        false,
    )?;
    require_expected_leaf_path(
        &capsule_file,
        &layout
            .maintenance_worker_capsule_file(&expected_capsule_sha256)
            .map_err(|_| AuthorityMaintenanceError("authority_worker_path_invalid"))?,
    )?;
    let capsule_bytes = read_held_file_bounded(&capsule_file, MAX_PROTECTED_RECEIPT_BYTES)?;
    MaintenanceWorkerCapsule::parse_canonical(&capsule_bytes, &expected_capsule_sha256)
}

fn open_native_worker_bootstrap_for_purpose(
    layout: &AuthorityLayout,
    expected_capsule_sha256: [u8; 32],
    purpose: NativeWorkerOpenPurpose,
) -> Result<(MaintenanceWorkerCapsule, NativeWorkerBootstrapStore), AuthorityMaintenanceError> {
    if expected_capsule_sha256.iter().all(|value| *value == 0) {
        return Err(AuthorityMaintenanceError(
            "authority_worker_capsule_digest_invalid",
        ));
    }
    let (binary_chain, binary_directory) =
        open_worker_binary_chain(layout, &expected_capsule_sha256)?;
    let (state_chain, state_directory) = open_worker_state_chain(layout, &expected_capsule_sha256)?;
    let worker_file = open_relative_file_existing(
        &binary_directory,
        WORKER_EXECUTABLE_NAME,
        BINARY_FILE_SDDL,
        FILE_SHARE_READ,
        false,
    )?;
    let capsule_file = open_relative_file_existing(
        &state_directory,
        CAPSULE_FILE_NAME,
        STATE_FILE_SDDL,
        FILE_SHARE_READ,
        false,
    )?;
    let capsule_bytes = read_held_file_bounded(&capsule_file, MAX_PROTECTED_RECEIPT_BYTES)?;
    let capsule =
        MaintenanceWorkerCapsule::parse_canonical(&capsule_bytes, &expected_capsule_sha256)?;
    let launch = MaintenanceWorkerLaunchContract::new(layout, &capsule)?;
    let intent_file = open_relative_file_existing(
        &state_directory,
        INTENT_FILE_NAME,
        STATE_FILE_SDDL,
        FILE_SHARE_READ,
        false,
    )?;
    let intent_bytes = read_held_file_bounded(&intent_file, MAX_PROTECTED_RECEIPT_BYTES)?;
    let intent = WorkerBootstrapIntentReceipt::parse_canonical(&intent_bytes, &capsule, &launch)?;
    let bootstrap_receipt_file = open_relative_file_existing(
        &state_directory,
        BOOTSTRAP_RECEIPT_FILE_NAME,
        STATE_FILE_SDDL,
        FILE_SHARE_READ,
        false,
    )?;
    let bootstrap_bytes =
        read_held_file_bounded(&bootstrap_receipt_file, MAX_PROTECTED_RECEIPT_BYTES)?;
    let bootstrap =
        WorkerBootstrapStagingReceipt::parse_canonical(&bootstrap_bytes, &capsule, &launch)?;
    let journal_file = open_relative_file_existing(
        &state_directory,
        JOURNAL_FILE_NAME,
        STATE_FILE_SDDL,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        true,
    )?;
    let journal_bytes = read_held_file_bounded(&journal_file, MAX_PROTECTED_RECEIPT_BYTES)?;
    let recovered = parse_worker_journal_recovery(&journal_bytes, expected_capsule_sha256)?;
    let records = recovered.records().to_vec();
    let journal_torn_tail = recovered.torn_tail();
    let has_phase = |phase| records.iter().any(|record| record.phase() == phase);
    let worker_started_record = records
        .iter()
        .find(|record| record.phase() == MaintenanceWorkerPhase::WorkerStarted);
    let source_bound_record = records
        .iter()
        .find(|record| record.phase() == MaintenanceWorkerPhase::SourceHandlesBound);
    let mut recovery_receipt_files = Vec::new();
    let mut recovery_receipts = NativeWorkerRecoveryReceipts::default();

    if has_phase(MaintenanceWorkerPhase::PipePrepared)
        || has_phase(MaintenanceWorkerPhase::ServiceCreated)
    {
        let (pipe_file, pipe_bytes) = open_worker_receipt_file(
            layout,
            &state_directory,
            &capsule,
            PIPE_PREPARED_RECEIPT_FILE_NAME,
        )?;
        let original_pipe = WorkerPipePreparedReceipt::parse_sealed_canonical(&pipe_bytes)?;
        original_pipe.validate(&capsule, &launch)?;
        let pipe_prepared_record = records
            .iter()
            .find(|record| record.phase() == MaintenanceWorkerPhase::PipePrepared);
        if let Some(record) = pipe_prepared_record {
            if record.phase_receipt_sha256()? != original_pipe.digest()? {
                return Err(AuthorityMaintenanceError(
                    "authority_worker_pipe_recovery_mismatch",
                ));
            }
        } else if !has_phase(MaintenanceWorkerPhase::ServiceCreated) {
            return Err(AuthorityMaintenanceError(
                "authority_worker_pipe_recovery_phase_invalid",
            ));
        }
        let pipe = if has_phase(MaintenanceWorkerPhase::PipeRecovered) {
            let (recovery_file, recovery_bytes) = open_worker_receipt_file(
                layout,
                &state_directory,
                &capsule,
                PIPE_RECOVERY_RECEIPT_FILE_NAME,
            )?;
            let recovery = WorkerPipeRecoveryReceipt::parse_sealed_canonical(&recovery_bytes)?;
            recovery.validate(&capsule, &launch, &records, &original_pipe)?;
            let replacement = recovery.replacement_pipe().clone();
            let recovered_record = records
                .iter()
                .find(|record| record.phase() == MaintenanceWorkerPhase::PipeRecovered)
                .ok_or(AuthorityMaintenanceError(
                    "authority_worker_pipe_recovery_phase_invalid",
                ))?;
            if recovered_record.phase_receipt_sha256()? != recovery.digest()? {
                return Err(AuthorityMaintenanceError(
                    "authority_worker_pipe_recovery_mismatch",
                ));
            }
            recovery_receipt_files.push(recovery_file);
            recovery_receipts.pipe_recovery = Some(recovery);
            replacement
        } else {
            original_pipe.clone()
        };
        recovery_receipt_files.push(pipe_file);
        recovery_receipts.original_pipe = Some(original_pipe);
        recovery_receipts.pipe = Some(pipe);
    }

    if has_phase(MaintenanceWorkerPhase::ServiceCreated) {
        let (service_file, service_bytes) = open_worker_receipt_file(
            layout,
            &state_directory,
            &capsule,
            SERVICE_CREATED_RECEIPT_FILE_NAME,
        )?;
        let service_created = ServiceCreatedReceipt::parse_sealed_canonical(&service_bytes)?;
        let pipe = recovery_receipts
            .pipe
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_missing",
            ))?;
        if service_created.pipe_prepared_receipt_sha256()? != pipe.digest()? {
            return Err(AuthorityMaintenanceError(
                "authority_worker_pipe_recovery_mismatch",
            ));
        }
        service_created.validate(&capsule, &launch, &bootstrap, pipe)?;
        recovery_receipt_files.push(service_file);
        recovery_receipts.service_created = Some(service_created);
    }

    if purpose == NativeWorkerOpenPurpose::FirstInvocation
        || has_phase(MaintenanceWorkerPhase::WorkerInvocationClaimed)
        || has_phase(MaintenanceWorkerPhase::WorkerStarted)
    {
        let (handoff_file, handoff_bytes) = open_worker_receipt_file(
            layout,
            &state_directory,
            &capsule,
            HANDOFF_RECEIPT_FILE_NAME,
        )?;
        let handoff = WorkerHandleHandoffReceipt::parse_canonical(&handoff_bytes, &capsule)?;
        let pipe = recovery_receipts
            .pipe
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_missing",
            ))?;
        handoff.validate_with_pipe(&capsule, &launch, pipe)?;
        recovery_receipt_files.push(handoff_file);
        recovery_receipts.handoff = Some(handoff);
    }

    if has_phase(MaintenanceWorkerPhase::WorkerInvocationClaimed) {
        let (claim_file, claim_bytes) = open_worker_receipt_file(
            layout,
            &state_directory,
            &capsule,
            WORKER_INVOCATION_CLAIM_RECEIPT_FILE_NAME,
        )?;
        let claim = WorkerInvocationClaimReceipt::parse_sealed_canonical(&claim_bytes)?;
        let pipe = recovery_receipts
            .pipe
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_missing",
            ))?;
        let service_created =
            recovery_receipts
                .service_created
                .as_ref()
                .ok_or(AuthorityMaintenanceError(
                    "authority_worker_recovery_evidence_missing",
                ))?;
        let handoff = recovery_receipts
            .handoff
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_missing",
            ))?;
        claim.validate(
            &capsule,
            &launch,
            &bootstrap,
            service_created,
            pipe,
            handoff,
            claim.worker(),
        )?;
        let claim_record = records
            .iter()
            .find(|record| record.phase() == MaintenanceWorkerPhase::WorkerInvocationClaimed)
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_missing",
            ))?;
        if claim_record.phase_receipt_sha256()? != claim.digest()? {
            return Err(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_mismatch",
            ));
        }
        recovery_receipt_files.push(claim_file);
        recovery_receipts.invocation_claim = Some(claim);
    }

    if has_phase(MaintenanceWorkerPhase::WorkerStarted) {
        let pipe = recovery_receipts
            .pipe
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_missing",
            ))?;
        let handoff = recovery_receipts
            .handoff
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_missing",
            ))?;
        let (started_file, started_bytes) = open_worker_receipt_file(
            layout,
            &state_directory,
            &capsule,
            WORKER_STARTED_RECEIPT_FILE_NAME,
        )?;
        let worker_started = WorkerStartedReceipt::parse_sealed_canonical(&started_bytes)?;
        worker_started.validate(
            &capsule,
            &bootstrap,
            recovery_receipts
                .service_created
                .as_ref()
                .ok_or(AuthorityMaintenanceError(
                    "authority_worker_recovery_evidence_missing",
                ))?,
            pipe,
            &handoff,
        )?;
        recovery_receipt_files.push(started_file);
        recovery_receipts.worker_started = Some(worker_started);
    }

    if has_phase(MaintenanceWorkerPhase::SourceStagingIntent) {
        let worker_started = worker_started_record.ok_or(AuthorityMaintenanceError(
            "authority_worker_recovery_evidence_missing",
        ))?;
        let handoff = recovery_receipts
            .handoff
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_missing",
            ))?;
        let (intent_file, intent_bytes) = open_worker_receipt_file(
            layout,
            &state_directory,
            &capsule,
            SOURCE_STAGING_INTENT_RECEIPT_FILE_NAME,
        )?;
        let staging_intent =
            WorkerSourceStagingIntentReceipt::parse_sealed_canonical(&intent_bytes)?;
        staging_intent.validate(&capsule, worker_started, handoff)?;
        recovery_receipt_files.push(intent_file);
        recovery_receipts.staging_intent = Some(staging_intent);
    }

    if has_phase(MaintenanceWorkerPhase::SourceStagingContained) {
        let intent = recovery_receipts
            .staging_intent
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_missing",
            ))?;
        let (cleanup_file, cleanup_bytes) = open_worker_receipt_file(
            layout,
            &state_directory,
            &capsule,
            PARTIAL_STAGING_CLEANUP_RECEIPT_FILE_NAME,
        )?;
        let cleanup = WorkerPartialStagingCleanupReceipt::parse_sealed_canonical(&cleanup_bytes)?;
        cleanup.validate(&capsule, intent)?;
        let cleanup_record = records
            .iter()
            .find(|record| record.phase() == MaintenanceWorkerPhase::SourceStagingContained)
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_missing",
            ))?;
        if cleanup_record.phase_receipt_sha256()? != cleanup.digest()? {
            return Err(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_mismatch",
            ));
        }
        recovery_receipt_files.push(cleanup_file);
        recovery_receipts.partial_staging_cleanup = Some(cleanup);
    }

    let source_store = if has_phase(MaintenanceWorkerPhase::SourceStagingContained) {
        None
    } else if let Some(source_bound) = source_bound_record {
        let worker_started = worker_started_record.ok_or(AuthorityMaintenanceError(
            "authority_worker_recovery_evidence_missing",
        ))?;
        let handoff = recovery_receipts
            .handoff
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_missing",
            ))?;
        let (staging_file, staging_bytes) = open_worker_receipt_file(
            layout,
            &state_directory,
            &capsule,
            SOURCE_STAGING_RECEIPT_FILE_NAME,
        )?;
        let staging = DurableSourceStagingReceipt::parse_canonical(
            &staging_bytes,
            &capsule,
            worker_started,
            handoff,
        )?;
        if source_bound.phase_receipt_sha256()? != staging.digest()? {
            return Err(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_mismatch",
            ));
        }
        let store = if has_phase(MaintenanceWorkerPhase::SourceStageResolved) {
            None
        } else {
            let store = open_native_worker_source_store(
                layout,
                &state_directory,
                &capsule,
                worker_started,
                handoff,
                &staging,
            )?;
            staging.validate_identity_ledger(
                &capsule,
                worker_started,
                handoff,
                &store.identity_ledger,
            )?;
            Some(store)
        };
        recovery_receipt_files.push(staging_file);
        recovery_receipts.staging = Some(staging);
        store
    } else {
        None
    };

    let mut nonce_consumption_lease = None;
    if has_phase(MaintenanceWorkerPhase::TransactionStarted) {
        let nonce = open_native_worker_nonce(layout, &capsule)?;
        let (started_file, started_bytes) = open_worker_receipt_file(
            layout,
            &state_directory,
            &capsule,
            TRANSACTION_STARTED_RECEIPT_FILE_NAME,
        )?;
        let transaction_started =
            TransactionStartedReceipt::parse_sealed_canonical(&started_bytes)?;
        recovery_receipt_files.push(started_file);
        recovery_receipts.nonce_consumption = Some(nonce.receipt().clone());
        recovery_receipts.transaction_started = Some(transaction_started);
        nonce_consumption_lease = Some(nonce);
    }

    let mut candidate_prepared = None;
    let mut candidate_armed = None;
    let mut incomplete_candidate_persistence = false;
    if has_phase(MaintenanceWorkerPhase::TransactionStarted) {
        let final_name = candidate_credential_file_name(&capsule.transaction_sha256()?)
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        let prepared_name = format!("{final_name}{CANDIDATE_PREPARED_SUFFIX}");
        candidate_prepared = open_candidate_credential_optional(
            layout,
            &prepared_name,
            CandidateCredentialPhase::Prepared,
        )?;
        candidate_armed = open_candidate_credential_optional(
            layout,
            &final_name,
            CandidateCredentialPhase::Armed,
        )?;
        let prepared_staging = open_candidate_credential_optional(
            layout,
            &format!("{prepared_name}{CANDIDATE_PRIVATE_STAGING_SUFFIX}"),
            CandidateCredentialPhase::Prepared,
        )?;
        let armed_staging = open_candidate_credential_optional(
            layout,
            &format!("{final_name}{CANDIDATE_PRIVATE_STAGING_SUFFIX}"),
            CandidateCredentialPhase::Armed,
        )?;
        let mut candidate_receipt = open_worker_receipt_file_optional(
            layout,
            &state_directory,
            &capsule,
            CANDIDATE_CREDENTIAL_ARMED_RECEIPT_FILE_NAME,
        )?
        .map(|(file, bytes)| {
            CandidateCredentialArmedReceipt::parse_sealed_canonical(&bytes)
                .map(|receipt| (file, receipt))
                .map_err(|_| {
                    AuthorityMaintenanceError(
                        "authority_candidate_credential_recovery_indeterminate",
                    )
                })
        })
        .transpose()?;
        if has_phase(MaintenanceWorkerPhase::CandidateCredentialArmed) {
            if prepared_staging.is_some() || armed_staging.is_some() {
                return Err(AuthorityMaintenanceError(
                    "authority_candidate_credential_recovery_indeterminate",
                ));
            }
            let prepared = candidate_prepared
                .as_ref()
                .ok_or(AuthorityMaintenanceError(
                    "authority_candidate_credential_recovery_indeterminate",
                ))?;
            let armed = candidate_armed.as_ref().ok_or(AuthorityMaintenanceError(
                "authority_candidate_credential_recovery_indeterminate",
            ))?;
            let (receipt_file, armed_receipt) = candidate_receipt.take().ok_or(
                AuthorityMaintenanceError("authority_candidate_credential_recovery_indeterminate"),
            )?;
            let started =
                recovery_receipts
                    .transaction_started
                    .as_ref()
                    .ok_or(AuthorityMaintenanceError(
                        "authority_worker_recovery_evidence_missing",
                    ))?;
            let worker_started =
                recovery_receipts
                    .worker_started
                    .as_ref()
                    .ok_or(AuthorityMaintenanceError(
                        "authority_worker_recovery_evidence_missing",
                    ))?;
            let nonce = nonce_consumption_lease
                .as_ref()
                .ok_or(AuthorityMaintenanceError(
                    "authority_worker_recovery_evidence_missing",
                ))?;
            validate_recovered_candidate_credential_pair(
                &capsule,
                started,
                worker_started,
                nonce,
                prepared,
                armed,
                &armed_receipt,
                &final_name,
            )?;
            if records
                .iter()
                .find(|record| record.phase() == MaintenanceWorkerPhase::CandidateCredentialArmed)
                .ok_or(AuthorityMaintenanceError(
                    "authority_worker_recovery_evidence_missing",
                ))?
                .phase_receipt_sha256()?
                != armed_receipt.digest()?
            {
                return Err(AuthorityMaintenanceError(
                    "authority_candidate_credential_recovery_indeterminate",
                ));
            }
            recovery_receipt_files.push(receipt_file);
            recovery_receipts.candidate_credential_armed = Some(armed_receipt);
        } else if candidate_prepared.is_some()
            && candidate_armed.is_some()
            && candidate_receipt.is_some()
            && prepared_staging.is_none()
            && armed_staging.is_none()
        {
            let prepared = candidate_prepared.as_ref().expect("checked above");
            let armed = candidate_armed.as_ref().expect("checked above");
            let (receipt_file, armed_receipt) = candidate_receipt.take().expect("checked above");
            let started =
                recovery_receipts
                    .transaction_started
                    .as_ref()
                    .ok_or(AuthorityMaintenanceError(
                        "authority_worker_recovery_evidence_missing",
                    ))?;
            let worker_started =
                recovery_receipts
                    .worker_started
                    .as_ref()
                    .ok_or(AuthorityMaintenanceError(
                        "authority_worker_recovery_evidence_missing",
                    ))?;
            let nonce = nonce_consumption_lease
                .as_ref()
                .ok_or(AuthorityMaintenanceError(
                    "authority_worker_recovery_evidence_missing",
                ))?;
            validate_recovered_candidate_credential_pair(
                &capsule,
                started,
                worker_started,
                nonce,
                prepared,
                armed,
                &armed_receipt,
                &final_name,
            )?;
            // This is the only bounded publication race: the final Armed file
            // and receipt are exact and durable, while the journal append has
            // not yet become visible. Expose the verified receipt so the
            // reader returns only `journal_not_current`; the transaction itself
            // remains containment-required and no mutation is replayed.
            recovery_receipt_files.push(receipt_file);
            recovery_receipts.candidate_credential_armed = Some(armed_receipt);
            incomplete_candidate_persistence = true;
        } else if candidate_prepared.is_some()
            || candidate_armed.is_some()
            || candidate_receipt.is_some()
            || prepared_staging.is_some()
            || armed_staging.is_some()
        {
            // A CreateNew candidate artifact without the authorizing journal
            // edge is never replayed. Recovery contains the transaction.
            incomplete_candidate_persistence = true;
        }
    }

    for (phase, name, committed) in [
        (
            MaintenanceWorkerPhase::TransactionCommitted,
            TRANSACTION_COMMITTED_RECEIPT_FILE_NAME,
            true,
        ),
        (
            MaintenanceWorkerPhase::TransactionContained,
            TRANSACTION_CONTAINED_RECEIPT_FILE_NAME,
            false,
        ),
    ] {
        if has_phase(phase) {
            let (file, bytes) = open_worker_receipt_file(layout, &state_directory, &capsule, name)?;
            if committed {
                recovery_receipts.transaction_committed =
                    Some(TransactionCommittedReceipt::parse_sealed_canonical(&bytes)?);
            } else {
                recovery_receipts.transaction_contained =
                    Some(TransactionContainedReceipt::parse_sealed_canonical(&bytes)?);
            }
            recovery_receipt_files.push(file);
        }
    }

    if has_phase(MaintenanceWorkerPhase::SourceStageResolved) {
        let staging = recovery_receipts
            .staging
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_missing",
            ))?;
        let terminal = records
            .iter()
            .find(|record| record.phase().is_transaction_terminal())
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_missing",
            ))?;
        let (cleanup_file, cleanup_bytes) = open_worker_receipt_file(
            layout,
            &state_directory,
            &capsule,
            STAGING_CLEANUP_RECEIPT_FILE_NAME,
        )?;
        let cleanup = WorkerStagingCleanupReceipt::parse_canonical(
            &cleanup_bytes,
            &capsule,
            staging,
            terminal,
        )?;
        recovery_receipt_files.push(cleanup_file);
        recovery_receipts.cleanup = Some(cleanup);
    }

    if has_phase(MaintenanceWorkerPhase::ExitReady) {
        let terminal = records
            .iter()
            .find(|record| record.phase().is_transaction_terminal())
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_missing",
            ))?;
        let cleanup = recovery_receipts
            .cleanup
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_missing",
            ))?;
        let worker_started =
            recovery_receipts
                .worker_started
                .as_ref()
                .ok_or(AuthorityMaintenanceError(
                    "authority_worker_recovery_evidence_missing",
                ))?;
        let (exit_ready_file, exit_ready_bytes) = open_worker_receipt_file(
            layout,
            &state_directory,
            &capsule,
            WORKER_EXIT_READY_RECEIPT_FILE_NAME,
        )?;
        let exit_ready = WorkerExitReadyReceipt::parse_sealed_canonical(&exit_ready_bytes)?;
        exit_ready.validate(&capsule, terminal, cleanup, worker_started)?;
        recovery_receipt_files.push(exit_ready_file);
        recovery_receipts.exit_ready = Some(exit_ready);
    }

    if has_phase(MaintenanceWorkerPhase::ServiceDeleteIntent) {
        let service_created =
            recovery_receipts
                .service_created
                .as_ref()
                .ok_or(AuthorityMaintenanceError(
                    "authority_worker_recovery_evidence_missing",
                ))?;
        let exit_ready = recovery_receipts
            .exit_ready
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_missing",
            ))?;
        let (intent_file, intent_bytes) = open_worker_receipt_file(
            layout,
            &state_directory,
            &capsule,
            SERVICE_DELETE_INTENT_RECEIPT_FILE_NAME,
        )?;
        let intent = ServiceDeleteIntentReceipt::parse_sealed_canonical(&intent_bytes)?;
        intent.validate(&capsule, &launch, service_created, exit_ready)?;
        recovery_receipt_files.push(intent_file);
        recovery_receipts.delete_intent = Some(intent);
    }

    if has_phase(MaintenanceWorkerPhase::ServiceDeletePending) {
        let (delete_file, delete_bytes) = open_worker_receipt_file(
            layout,
            &state_directory,
            &capsule,
            SERVICE_DELETE_PENDING_RECEIPT_FILE_NAME,
        )?;
        recovery_receipts.delete_pending = Some(
            ServiceDeletePendingReceipt::parse_sealed_canonical(&delete_bytes)?,
        );
        recovery_receipt_files.push(delete_file);
    }

    if has_phase(MaintenanceWorkerPhase::FinalizerHandlesClosed) {
        let exit_ready = recovery_receipts
            .exit_ready
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_missing",
            ))?;
        let delete_pending =
            recovery_receipts
                .delete_pending
                .as_ref()
                .ok_or(AuthorityMaintenanceError(
                    "authority_worker_recovery_evidence_missing",
                ))?;
        let (closed_file, closed_bytes) = open_worker_receipt_file(
            layout,
            &state_directory,
            &capsule,
            FINALIZER_HANDLES_CLOSED_RECEIPT_FILE_NAME,
        )?;
        let closed = FinalizerHandlesClosedReceipt::parse_sealed_canonical(&closed_bytes)?;
        closed.validate(&capsule, exit_ready, delete_pending)?;
        recovery_receipt_files.push(closed_file);
        recovery_receipts.handles_closed = Some(closed);
    }

    if has_phase(MaintenanceWorkerPhase::ServiceAbsent) {
        let staging = recovery_receipts
            .staging
            .as_ref()
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_missing",
            ))?;
        let terminal = records
            .iter()
            .find(|record| record.phase().is_transaction_terminal())
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_recovery_evidence_missing",
            ))?;
        let mut cleanup_file = None;
        let cleanup = match recovery_receipts.cleanup.clone() {
            Some(cleanup) => cleanup,
            None => {
                let (file, cleanup_bytes) = open_worker_receipt_file(
                    layout,
                    &state_directory,
                    &capsule,
                    STAGING_CLEANUP_RECEIPT_FILE_NAME,
                )?;
                let cleanup = WorkerStagingCleanupReceipt::parse_canonical(
                    &cleanup_bytes,
                    &capsule,
                    staging,
                    terminal,
                )?;
                cleanup_file = Some(file);
                cleanup
            }
        };
        let (absent_file, absent_bytes) = open_worker_receipt_file(
            layout,
            &state_directory,
            &capsule,
            SERVICE_ABSENT_RECEIPT_FILE_NAME,
        )?;
        recovery_receipts.cleanup = Some(cleanup);
        recovery_receipts.service_absent =
            Some(ServiceAbsentReceipt::parse_sealed_canonical(&absent_bytes)?);
        if let Some(cleanup_file) = cleanup_file {
            recovery_receipt_files.push(cleanup_file);
        }
        recovery_receipt_files.push(absent_file);
    }
    let now_unix_millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| AuthorityMaintenanceError("authority_worker_clock_invalid"))?
        .as_millis()
        .try_into()
        .map_err(|_| AuthorityMaintenanceError("authority_worker_clock_invalid"))?;
    let live_worker = if recovery_receipts.worker_started.is_some()
        && !has_phase(MaintenanceWorkerPhase::TransactionStarted)
    {
        Some(super::worker_windows::recover_live_worker_scm_readback(
            &capsule,
            &launch,
            recovery_receipts
                .worker_started
                .as_ref()
                .ok_or(AuthorityMaintenanceError(
                    "authority_worker_recovery_evidence_missing",
                ))?,
        )?)
    } else {
        None
    };
    let recovery_evidence = || super::worker::WorkerRecoveryEvidence {
        intent: Some(&intent),
        bootstrap: Some(&bootstrap),
        original_pipe: recovery_receipts.original_pipe.as_ref(),
        pipe: recovery_receipts.pipe.as_ref(),
        pipe_recovery: recovery_receipts.pipe_recovery.as_ref(),
        service_created: recovery_receipts.service_created.as_ref(),
        handoff: recovery_receipts.handoff.as_ref(),
        invocation_claim: recovery_receipts.invocation_claim.as_ref(),
        worker_started: recovery_receipts.worker_started.as_ref(),
        live_worker: live_worker.as_ref(),
        staging_intent: recovery_receipts.staging_intent.as_ref(),
        partial_staging_cleanup: recovery_receipts.partial_staging_cleanup.as_ref(),
        staging: recovery_receipts.staging.as_ref(),
        nonce_consumption: recovery_receipts.nonce_consumption.as_ref(),
        transaction_started: recovery_receipts.transaction_started.as_ref(),
        candidate_prepared: candidate_prepared
            .as_ref()
            .map(NativeCandidateCredentialLease::record),
        candidate_credential_armed: recovery_receipts.candidate_credential_armed.as_ref(),
        candidate_armed: candidate_armed
            .as_ref()
            .map(NativeCandidateCredentialLease::record),
        transaction_committed: recovery_receipts.transaction_committed.as_ref(),
        transaction_contained: recovery_receipts.transaction_contained.as_ref(),
        exit_ready: recovery_receipts.exit_ready.as_ref(),
        delete_intent: recovery_receipts.delete_intent.as_ref(),
        delete_pending: recovery_receipts.delete_pending.as_ref(),
        handles_closed: recovery_receipts.handles_closed.as_ref(),
        cleanup: recovery_receipts.cleanup.as_ref(),
        service_absent: recovery_receipts.service_absent.as_ref(),
    };
    let recovery_disposition = match purpose {
        NativeWorkerOpenPurpose::FirstInvocation => {
            if journal_torn_tail
                || records.last().map(MaintenanceWorkerJournalRecord::phase)
                    != Some(MaintenanceWorkerPhase::ServiceCreated)
                || recovery_receipts.handoff.is_none()
                || recovery_receipts.invocation_claim.is_some()
            {
                return Err(AuthorityMaintenanceError(
                    "authority_worker_first_invocation_not_claimable",
                ));
            }
            super::worker::validate_worker_recovery_bundle(
                &capsule,
                &launch,
                &records,
                now_unix_millis,
                recovery_evidence(),
            )?
        }
        NativeWorkerOpenPurpose::Recovery => {
            super::worker::validate_worker_recovery_bundle_with_containment(
                &capsule,
                &launch,
                &records,
                now_unix_millis,
                journal_torn_tail,
                recovery_evidence(),
            )?
        }
    };
    let recovery_requires_containment = incomplete_candidate_persistence
        || matches!(
        recovery_disposition,
        super::worker::MaintenanceWorkerRecoveryDisposition::ContainSameCapsuleBeforeTransaction
            | super::worker::MaintenanceWorkerRecoveryDisposition::ContainInterruptedTransaction
    );

    let worker_descriptor = AuthorityPayloadDigest::new(
        capsule.install_helper_sha256()?,
        capsule.install_helper_byte_length(),
    )?;
    let worker_identity = file_identity(&worker_file)?;
    verify_exact_file_identity(&worker_identity, worker_descriptor.byte_length())?;
    let worker_digest = hash_held_file(&worker_file, worker_descriptor.byte_length())?;
    if worker_digest != *worker_descriptor.sha256() {
        return Err(AuthorityMaintenanceError(
            "authority_worker_image_readback_mismatch",
        ));
    }
    let worker_binding = WorkerBootstrapStagedFileBinding::from_observed(
        "install-helper",
        WORKER_EXECUTABLE_NAME,
        worker_digest,
        worker_descriptor.byte_length(),
        worker_identity.volume_serial,
        worker_identity.file_id,
        worker_bootstrap_file_readback_receipt(
            "install-helper",
            &worker_digest,
            worker_descriptor.byte_length(),
            worker_identity.volume_serial,
            &worker_identity.file_id,
        ),
    );
    let capsule_descriptor = AuthorityPayloadDigest::new(
        Sha256::digest(&capsule_bytes).into(),
        capsule_bytes.len() as u64,
    )?;
    let capsule_identity = file_identity(&capsule_file)?;
    verify_exact_file_identity(&capsule_identity, capsule_descriptor.byte_length())?;
    let capsule_binding = WorkerBootstrapStagedFileBinding::from_observed(
        "capsule",
        CAPSULE_FILE_NAME,
        *capsule_descriptor.sha256(),
        capsule_descriptor.byte_length(),
        capsule_identity.volume_serial,
        capsule_identity.file_id,
        worker_bootstrap_file_readback_receipt(
            "capsule",
            capsule_descriptor.sha256(),
            capsule_descriptor.byte_length(),
            capsule_identity.volume_serial,
            &capsule_identity.file_id,
        ),
    );
    let binary_identity = file_identity(&binary_directory)?;
    let state_identity = file_identity(&state_directory)?;
    bootstrap.validate_reopened_files(
        &capsule,
        &launch,
        binary_identity.volume_serial,
        binary_identity.file_id,
        state_identity.volume_serial,
        state_identity.file_id,
        &worker_binding,
        &capsule_binding,
    )?;

    Ok((
        capsule,
        NativeWorkerBootstrapStore {
            _binary_chain: binary_chain,
            _state_chain: state_chain,
            binary_directory,
            state_directory,
            _worker_file: worker_file,
            _capsule_file: capsule_file,
            _intent_file: intent_file,
            _bootstrap_receipt_file: bootstrap_receipt_file,
            recovery_receipt_files,
            nonce_consumption_lease,
            journal_file,
            launch,
            intent,
            bootstrap,
            recovery_receipts,
            candidate_prepared,
            candidate_armed,
            source_store,
            records,
            journal_torn_tail,
            recovery_requires_containment,
        },
    ))
}

fn open_native_worker_source_store(
    layout: &AuthorityLayout,
    state_directory: &OwnedHandle,
    capsule: &MaintenanceWorkerCapsule,
    worker_started: &MaintenanceWorkerJournalRecord,
    handoff: &WorkerHandleHandoffReceipt,
    staging: &DurableSourceStagingReceipt,
) -> Result<NativeWorkerSourceStore, AuthorityMaintenanceError> {
    let capsule_sha256 = capsule.digest()?;
    let stage_relative_name = format!("stage.{}", hex_lower(&capsule_sha256));
    let (stage_directory, created) = nt_open_relative(
        state_directory,
        &stage_relative_name,
        STATE_DIRECTORY_SDDL,
        true,
        FILE_OPEN,
        DIRECTORY_READ_ACCESS | ACCESS_SYSTEM_SECURITY,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
    )?;
    if created {
        return Err(AuthorityMaintenanceError(
            "authority_worker_recovery_created_path",
        ));
    }
    verify_protected_directory(&stage_directory, STATE_GENERATION_DIRECTORY_SDDL)?;
    require_expected_leaf_path(
        &stage_directory,
        &layout
            .maintenance_worker_source_stage_root(&capsule_sha256)
            .map_err(|_| AuthorityMaintenanceError("authority_worker_path_invalid"))?,
    )?;

    let identity_ledger_file = open_relative_file_existing(
        &stage_directory,
        SOURCE_IDENTITY_LEDGER_FILE_NAME,
        STATE_FILE_SDDL,
        FILE_SHARE_READ,
        false,
    )?;
    require_expected_leaf_path(
        &identity_ledger_file,
        &layout
            .maintenance_worker_source_identity_ledger_file(&capsule_sha256)
            .map_err(|_| AuthorityMaintenanceError("authority_worker_path_invalid"))?,
    )?;
    let identity_ledger_bytes =
        read_held_file_bounded(&identity_ledger_file, MAX_PROTECTED_RECEIPT_BYTES)?;
    let identity_ledger = WorkerSourceIdentityLedger::parse_canonical(
        &identity_ledger_bytes,
        capsule,
        worker_started,
        handoff,
    )?;
    staging.validate_identity_ledger(capsule, worker_started, handoff, &identity_ledger)?;
    let stage_identity = file_identity(&stage_directory)?;
    if identity_ledger.staging_identity()? != (stage_identity.volume_serial, stage_identity.file_id)
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_source_stage_identity_mismatch",
        ));
    }

    let mut staged_files = Vec::with_capacity(STAGED_PAYLOAD_KINDS.len());
    for kind in STAGED_PAYLOAD_KINDS {
        let binding = identity_ledger.payload(kind);
        let file = open_relative_file_existing(
            &stage_directory,
            binding.staging_relative_name(),
            STATE_FILE_SDDL,
            FILE_SHARE_READ,
            false,
        )?;
        require_expected_leaf_path(
            &file,
            &layout
                .maintenance_worker_source_stage_root(&capsule_sha256)
                .map_err(|_| AuthorityMaintenanceError("authority_worker_path_invalid"))?
                .join(binding.staging_relative_name()),
        )?;
        let identity = file_identity(&file)?;
        let expected = capsule.payload_source_expectation(kind)?;
        verify_exact_file_identity(&identity, expected.descriptor.byte_length())?;
        let digest = hash_held_file(&file, expected.descriptor.byte_length())?;
        if file_identity(&file)? != identity {
            return Err(AuthorityMaintenanceError(
                "authority_worker_durable_staging_readback_mismatch",
            ));
        }
        binding.validate_reopened_file(
            kind,
            capsule,
            identity.volume_serial,
            identity.file_id,
            identity.byte_length,
            digest,
        )?;
        staged_files.push(file);
    }
    let staged_files: [OwnedHandle; PROTECTED_GENERATION_PAYLOAD_COUNT] =
        staged_files.try_into().map_err(|_| {
            AuthorityMaintenanceError("authority_worker_durable_staging_readback_mismatch")
        })?;
    Ok(NativeWorkerSourceStore {
        _directory: stage_directory,
        _source_handles: None,
        _staged_files: staged_files,
        _identity_ledger_file: identity_ledger_file,
        identity_ledger,
    })
}

fn open_worker_binary_chain(
    layout: &AuthorityLayout,
    capsule_sha256: &[u8; 32],
) -> Result<(Vec<OwnedHandle>, OwnedHandle), AuthorityMaintenanceError> {
    let expected = layout
        .maintenance_worker_root(capsule_sha256)
        .map_err(|_| AuthorityMaintenanceError("authority_worker_path_invalid"))?;
    let (chain, leaf) = open_protected_chain(
        layout.binary_base(),
        &["v1", "maintenance"],
        &hex_lower(capsule_sha256),
        BINARY_DIRECTORY_SDDL,
        BINARY_GENERATION_DIRECTORY_SDDL,
    )?;
    require_expected_leaf_path(&leaf, &expected)?;
    Ok((chain, leaf))
}

fn open_worker_state_chain(
    layout: &AuthorityLayout,
    capsule_sha256: &[u8; 32],
) -> Result<(Vec<OwnedHandle>, OwnedHandle), AuthorityMaintenanceError> {
    let expected = layout
        .maintenance_worker_state_root(capsule_sha256)
        .map_err(|_| AuthorityMaintenanceError("authority_worker_path_invalid"))?;
    let (chain, leaf) = open_protected_chain(
        layout.state_base(),
        &["v1", "maintenance"],
        &hex_lower(capsule_sha256),
        STATE_DIRECTORY_SDDL,
        STATE_GENERATION_DIRECTORY_SDDL,
    )?;
    require_expected_leaf_path(&leaf, &expected)?;
    Ok((chain, leaf))
}

fn persist_candidate_credential_create_new(
    layout: &AuthorityLayout,
    relative_name: &str,
    expected_phase: CandidateCredentialPhase,
    record: CandidateCredentialRecord,
) -> Result<NativeCandidateCredentialLease, AuthorityMaintenanceError> {
    validate_candidate_credential_name(relative_name)?;
    if record.phase() != expected_phase {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_credential_phase_invalid",
        ));
    }
    let bytes = record
        .canonical_bytes()
        .map_err(|error| AuthorityMaintenanceError(error.code()))?;
    if bytes.is_empty() || bytes.len() as u64 > MAX_CANDIDATE_CREDENTIAL_BYTES {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_credential_size_invalid",
        ));
    }
    let expected_root = layout.candidate_activation_root();
    // The elevated bootstrap actor must provision this exact candidate namespace.
    // The SYSTEM worker opens only that directory and never obtains create-child
    // authority over the broad state anchor.
    let directory = open_absolute_protected_directory(
        &expected_root,
        CANDIDATE_ACTIVATION_DIRECTORY_SDDL,
        CANDIDATE_PUBLICATION_DIRECTORY_ACCESS | ACCESS_SYSTEM_SECURITY,
    )?;
    let directory_identity = file_identity(&directory)?;
    let staging_name = format!("{relative_name}{CANDIDATE_PRIVATE_STAGING_SUFFIX}");
    validate_candidate_credential_name(&staging_name)?;
    let mut writable_directory = Some(directory);
    let mut staging_file = None;
    let mut staged_identity = None;
    let mut readonly_directory = None;
    let mut readonly_file = None;
    let mut readonly_directory_identity = None;
    let mut published_identity = None;
    run_candidate_atomic_publication(|phase| match phase {
        NativeCandidateAtomicPublicationPhase::CreatePrivateStaging => {
            let parent = writable_directory
                .as_ref()
                .ok_or(AuthorityMaintenanceError(
                    "authority_candidate_credential_persistence_indeterminate",
                ))?;
            let (file, created) = nt_open_relative(
                parent,
                &staging_name,
                STATE_FILE_SDDL,
                false,
                FILE_CREATE,
                CANDIDATE_PRIVATE_STAGING_FILE_ACCESS | ACCESS_SYSTEM_SECURITY,
                FILE_SHARE_READ,
            )?;
            if !created {
                return Err(AuthorityMaintenanceError(
                    "authority_candidate_credential_persistence_indeterminate",
                ));
            }
            verify_protected_file(&file, STATE_FILE_SDDL)?;
            require_expected_leaf_path(&file, &expected_root.join(&staging_name))?;
            let identity = file_identity(&file)?;
            if identity.byte_length != 0
                || identity.volume_serial != directory_identity.volume_serial
            {
                return Err(AuthorityMaintenanceError(
                    "authority_candidate_credential_persistence_indeterminate",
                ));
            }
            staged_identity = Some(identity);
            staging_file = Some(file);
            Ok(())
        }
        NativeCandidateAtomicPublicationPhase::WriteExact => write_held_file_exact(
            staging_file.as_ref().ok_or(AuthorityMaintenanceError(
                "authority_candidate_credential_persistence_indeterminate",
            ))?,
            &bytes,
        ),
        NativeCandidateAtomicPublicationPhase::FlushStaging => flush_handle(
            staging_file.as_ref().ok_or(AuthorityMaintenanceError(
                "authority_candidate_credential_persistence_indeterminate",
            ))?,
            "authority_candidate_credential_staging_flush_failed",
        ),
        NativeCandidateAtomicPublicationPhase::VerifyPrivateStaging => {
            let file = staging_file.as_ref().ok_or(AuthorityMaintenanceError(
                "authority_candidate_credential_persistence_indeterminate",
            ))?;
            verify_protected_file(file, STATE_FILE_SDDL)?;
            require_expected_leaf_path(file, &expected_root.join(&staging_name))?;
            let identity = file_identity(file)?;
            verify_exact_file_identity(&identity, bytes.len() as u64)?;
            if staged_identity.is_none_or(|before| {
                before.volume_serial != identity.volume_serial
                    || before.file_id != identity.file_id
                    || before.link_count != identity.link_count
                    || before.attributes != identity.attributes
            }) || read_held_file_bounded(file, MAX_CANDIDATE_CREDENTIAL_BYTES as usize)? != bytes
                || file_identity(file)? != identity
            {
                return Err(AuthorityMaintenanceError(
                    "authority_candidate_credential_persistence_indeterminate",
                ));
            }
            staged_identity = Some(identity);
            Ok(())
        }
        NativeCandidateAtomicPublicationPhase::RenameNoReplace => {
            let file = staging_file.as_ref().ok_or(AuthorityMaintenanceError(
                "authority_candidate_credential_persistence_indeterminate",
            ))?;
            let parent = writable_directory
                .as_ref()
                .ok_or(AuthorityMaintenanceError(
                    "authority_candidate_credential_persistence_indeterminate",
                ))?;
            rename_relative_no_replace(file, parent, relative_name)?;
            require_expected_leaf_path(file, &expected_root.join(relative_name))?;
            let identity = file_identity(file)?;
            if Some(identity) != staged_identity {
                return Err(AuthorityMaintenanceError(
                    "authority_candidate_credential_persistence_indeterminate",
                ));
            }
            published_identity = Some(identity);
            Ok(())
        }
        NativeCandidateAtomicPublicationPhase::FlushParent => flush_handle(
            writable_directory
                .as_ref()
                .ok_or(AuthorityMaintenanceError(
                    "authority_candidate_credential_persistence_indeterminate",
                ))?,
            "authority_candidate_credential_parent_flush_failed",
        ),
        NativeCandidateAtomicPublicationPhase::CloseWritableHandles => {
            drop(staging_file.take());
            drop(writable_directory.take());
            Ok(())
        }
        NativeCandidateAtomicPublicationPhase::ReopenReadOnly => {
            let directory = open_absolute_protected_directory(
                &expected_root,
                CANDIDATE_ACTIVATION_DIRECTORY_SDDL,
                CANDIDATE_READONLY_DIRECTORY_ACCESS | ACCESS_SYSTEM_SECURITY,
            )?;
            let reopened_directory_identity = file_identity(&directory)?;
            if reopened_directory_identity != directory_identity {
                return Err(AuthorityMaintenanceError(
                    "authority_candidate_credential_persistence_indeterminate",
                ));
            }
            let (file, created) = nt_open_relative(
                &directory,
                relative_name,
                STATE_FILE_SDDL,
                false,
                FILE_OPEN,
                CANDIDATE_READONLY_FILE_ACCESS | ACCESS_SYSTEM_SECURITY,
                FILE_SHARE_READ,
            )?;
            if created {
                return Err(AuthorityMaintenanceError(
                    "authority_candidate_credential_persistence_indeterminate",
                ));
            }
            verify_protected_file(&file, STATE_FILE_SDDL)?;
            require_expected_leaf_path(&file, &expected_root.join(relative_name))?;
            readonly_directory_identity = Some(reopened_directory_identity);
            readonly_directory = Some(directory);
            readonly_file = Some(file);
            Ok(())
        }
        NativeCandidateAtomicPublicationPhase::VerifyPublished => {
            let file = readonly_file.as_ref().ok_or(AuthorityMaintenanceError(
                "authority_candidate_credential_persistence_indeterminate",
            ))?;
            let identity = file_identity(file)?;
            verify_exact_file_identity(&identity, bytes.len() as u64)?;
            if Some(identity) != published_identity
                || read_held_file_bounded(file, MAX_CANDIDATE_CREDENTIAL_BYTES as usize)? != bytes
                || file_identity(file)? != identity
            {
                return Err(AuthorityMaintenanceError(
                    "authority_candidate_credential_persistence_indeterminate",
                ));
            }
            Ok(())
        }
    })
    .map_err(|_| {
        AuthorityMaintenanceError("authority_candidate_credential_persistence_indeterminate")
    })?;
    let directory = readonly_directory.ok_or(AuthorityMaintenanceError(
        "authority_candidate_credential_persistence_indeterminate",
    ))?;
    let file = readonly_file.ok_or(AuthorityMaintenanceError(
        "authority_candidate_credential_persistence_indeterminate",
    ))?;
    let file_identity = published_identity.ok_or(AuthorityMaintenanceError(
        "authority_candidate_credential_persistence_indeterminate",
    ))?;
    let lease = NativeCandidateCredentialLease {
        _directory: directory,
        _file: file,
        directory_identity: readonly_directory_identity.ok_or(AuthorityMaintenanceError(
            "authority_candidate_credential_persistence_indeterminate",
        ))?,
        record,
        file_identity,
        bytes_sha256: Sha256::digest(&bytes).into(),
    };
    verify_candidate_credential_lease(layout, relative_name, expected_phase, &lease).map_err(
        |_| AuthorityMaintenanceError("authority_candidate_credential_persistence_indeterminate"),
    )?;
    Ok(lease)
}

fn rename_relative_no_replace(
    file: &OwnedHandle,
    parent: &OwnedHandle,
    destination_name: &str,
) -> Result<(), AuthorityMaintenanceError> {
    validate_candidate_credential_name(destination_name)?;
    let name = destination_name.encode_utf16().collect::<Vec<_>>();
    let name_byte_length = name
        .len()
        .checked_mul(size_of::<u16>())
        .and_then(|value| u32::try_from(value).ok())
        .ok_or(AuthorityMaintenanceError(
            "authority_candidate_credential_path_invalid",
        ))?;
    let file_name_offset = std::mem::offset_of!(FILE_RENAME_INFORMATION, FileName);
    let total_length = file_name_offset
        .checked_add(name_byte_length as usize)
        .ok_or(AuthorityMaintenanceError(
            "authority_candidate_credential_path_invalid",
        ))?;
    let storage_words = total_length
        .checked_add(size_of::<usize>() - 1)
        .map(|value| value / size_of::<usize>())
        .ok_or(AuthorityMaintenanceError(
            "authority_candidate_credential_path_invalid",
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
    if status_code < 0 {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_credential_publish_failed",
        ));
    }
    Ok(())
}

fn verify_candidate_credential_lease(
    layout: &AuthorityLayout,
    relative_name: &str,
    expected_phase: CandidateCredentialPhase,
    lease: &NativeCandidateCredentialLease,
) -> Result<(), AuthorityMaintenanceError> {
    validate_candidate_credential_name(relative_name)?;
    let expected_root = layout.candidate_activation_root();
    verify_protected_directory(&lease._directory, CANDIDATE_ACTIVATION_DIRECTORY_SDDL)?;
    require_expected_leaf_path(&lease._directory, &expected_root)?;
    if file_identity(&lease._directory)? != lease.directory_identity {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_credential_directory_identity_changed",
        ));
    }
    verify_protected_file(&lease._file, STATE_FILE_SDDL)?;
    require_expected_leaf_path(&lease._file, &expected_root.join(relative_name))?;
    let bytes = lease
        .record
        .canonical_bytes()
        .map_err(|error| AuthorityMaintenanceError(error.code()))?;
    let identity = file_identity(&lease._file)?;
    verify_exact_file_identity(&identity, bytes.len() as u64)?;
    let parsed = CandidateCredentialRecord::parse_canonical(&bytes)
        .map_err(|error| AuthorityMaintenanceError(error.code()))?;
    if lease.record.phase() != expected_phase
        || parsed != lease.record
        || identity != lease.file_identity
        || identity.volume_serial != lease.directory_identity.volume_serial
        || read_held_file_bounded(&lease._file, MAX_CANDIDATE_CREDENTIAL_BYTES as usize)? != bytes
        || file_identity(&lease._file)? != identity
        || <[u8; 32]>::from(Sha256::digest(&bytes)) != lease.bytes_sha256
        || lease.bytes_sha256.iter().all(|value| *value == 0)
    {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_credential_readback_mismatch",
        ));
    }
    Ok(())
}

fn open_candidate_credential_optional(
    layout: &AuthorityLayout,
    relative_name: &str,
    expected_phase: CandidateCredentialPhase,
) -> Result<Option<NativeCandidateCredentialLease>, AuthorityMaintenanceError> {
    validate_candidate_credential_name(relative_name)?;
    let expected_root = layout.candidate_activation_root();
    let Some(directory) = open_absolute_protected_directory_optional(
        &expected_root,
        CANDIDATE_ACTIVATION_DIRECTORY_SDDL,
        CANDIDATE_READONLY_DIRECTORY_ACCESS | ACCESS_SYSTEM_SECURITY,
    )
    .map_err(|_| {
        AuthorityMaintenanceError("authority_candidate_credential_recovery_indeterminate")
    })?
    else {
        return Ok(None);
    };
    let directory_identity = file_identity(&directory).map_err(|_| {
        AuthorityMaintenanceError("authority_candidate_credential_recovery_indeterminate")
    })?;
    let Some(file) = nt_open_relative_optional(
        &directory,
        relative_name,
        STATE_FILE_SDDL,
        false,
        CANDIDATE_READONLY_FILE_ACCESS | ACCESS_SYSTEM_SECURITY,
        FILE_SHARE_READ,
    )
    .map_err(|_| {
        AuthorityMaintenanceError("authority_candidate_credential_recovery_indeterminate")
    })?
    else {
        return Ok(None);
    };
    let result: Result<NativeCandidateCredentialLease, AuthorityMaintenanceError> = (|| {
        verify_protected_file(&file, STATE_FILE_SDDL)?;
        require_expected_leaf_path(&file, &expected_root.join(relative_name))?;
        let bytes = read_held_file_bounded(&file, MAX_CANDIDATE_CREDENTIAL_BYTES as usize)?;
        let record = CandidateCredentialRecord::parse_canonical(&bytes)
            .map_err(|error| AuthorityMaintenanceError(error.code()))?;
        let file_identity = file_identity(&file)?;
        let lease = NativeCandidateCredentialLease {
            _directory: directory,
            _file: file,
            directory_identity,
            record,
            file_identity,
            bytes_sha256: Sha256::digest(&bytes).into(),
        };
        verify_candidate_credential_lease(layout, relative_name, expected_phase, &lease)?;
        Ok(lease)
    })();
    result.map(Some).map_err(|_| {
        AuthorityMaintenanceError("authority_candidate_credential_recovery_indeterminate")
    })
}

fn open_worker_receipt_file_optional(
    layout: &AuthorityLayout,
    state_directory: &OwnedHandle,
    capsule: &MaintenanceWorkerCapsule,
    name: &str,
) -> Result<Option<(OwnedHandle, Vec<u8>)>, AuthorityMaintenanceError> {
    let Some(file) = nt_open_relative_optional(
        state_directory,
        name,
        STATE_FILE_SDDL,
        false,
        FILE_READ_ACCESS | ACCESS_SYSTEM_SECURITY,
        FILE_SHARE_READ,
    )?
    else {
        return Ok(None);
    };
    verify_protected_file(&file, STATE_FILE_SDDL)?;
    require_worker_receipt_path(layout, capsule, &file, name)?;
    let bytes = read_held_file_bounded(&file, MAX_PROTECTED_RECEIPT_BYTES)?;
    Ok(Some((file, bytes)))
}

fn validate_candidate_credential_name(name: &str) -> Result<(), AuthorityMaintenanceError> {
    if name.is_empty()
        || name.len() > 255
        || name == "."
        || name == ".."
        || name.contains('/')
        || name.contains('\\')
        || name.contains(':')
        || name.chars().any(char::is_control)
    {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_credential_path_invalid",
        ));
    }
    Ok(())
}

fn validate_recovered_prepared_candidate_binding(
    capsule: &MaintenanceWorkerCapsule,
    transaction_started: &TransactionStartedReceipt,
    worker_started: &WorkerStartedReceipt,
    nonce: &NativeNonceConsumptionLease,
    prepared: &CandidateCredentialRecord,
) -> Result<(), AuthorityMaintenanceError> {
    if prepared.phase() != CandidateCredentialPhase::Prepared {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_prepared_phase_invalid",
        ));
    }
    let binding = prepared
        .binding()
        .map_err(|error| AuthorityMaintenanceError(error.code()))?;
    let issuer = binding.issuer();
    let (
        nonce_consumption_receipt_sha256,
        nonce_consumption_full_readback_sha256,
        nonce_consumption_file_sha256,
        nonce_consumption_file_volume_serial,
        nonce_consumption_file_id,
    ) = nonce.durable_binding(capsule)?;
    if *binding.plan_sha256() != capsule.plan_sha256()?
        || *binding.generation() != capsule.generation()?
        || *binding.transaction_sha256() != capsule.transaction_sha256()?
        || *issuer.capsule_sha256() != capsule.digest()?
        || *issuer.transaction_started_receipt_sha256() != transaction_started.digest()?
        || *issuer.worker_started_receipt_sha256() != worker_started.digest()?
        || !worker_started.matches_candidate_process_evidence(issuer.maintenance_worker())?
        || *issuer.nonce_consumption_receipt_sha256() != nonce_consumption_receipt_sha256
        || *issuer.nonce_consumption_full_readback_sha256()
            != nonce_consumption_full_readback_sha256
        || *issuer.nonce_consumption_file_sha256() != nonce_consumption_file_sha256
        || issuer.nonce_consumption_file_volume_serial() != nonce_consumption_file_volume_serial
        || *issuer.nonce_consumption_file_id() != nonce_consumption_file_id
    {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_prepared_binding_mismatch",
        ));
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn validate_recovered_candidate_credential_pair(
    capsule: &MaintenanceWorkerCapsule,
    transaction_started: &TransactionStartedReceipt,
    worker_started: &WorkerStartedReceipt,
    nonce: &NativeNonceConsumptionLease,
    prepared: &NativeCandidateCredentialLease,
    armed: &NativeCandidateCredentialLease,
    armed_receipt: &CandidateCredentialArmedReceipt,
    final_name: &str,
) -> Result<(), AuthorityMaintenanceError> {
    validate_recovered_prepared_candidate_binding(
        capsule,
        transaction_started,
        worker_started,
        nonce,
        prepared.record(),
    )?;
    armed_receipt.validate(
        capsule,
        transaction_started,
        worker_started,
        prepared.record(),
    )?;
    if !armed_receipt.matches_prepared_persistence(
        prepared.record(),
        prepared.file_identity.volume_serial,
        &prepared.file_identity.file_id,
        &prepared.bytes_sha256,
    )? || armed.record().phase() != CandidateCredentialPhase::Armed
        || armed
            .record()
            .binding()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?
            != prepared
                .record()
                .binding()
                .map_err(|error| AuthorityMaintenanceError(error.code()))?
        || armed
            .record()
            .credential_sha256()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?
            != prepared
                .record()
                .credential_sha256()
                .map_err(|error| AuthorityMaintenanceError(error.code()))?
        || armed
            .record()
            .armed_receipt_sha256()
            .map_err(|error| AuthorityMaintenanceError(error.code()))?
            != armed_receipt.digest()?
        || armed.record().candidate_service() != Some(armed_receipt.candidate_service())
        || armed_receipt.armed_relative_name() != final_name
    {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_credential_recovery_indeterminate",
        ));
    }
    Ok(())
}

fn open_protected_chain(
    protected_base_path: &Path,
    components: &[&str],
    leaf: &str,
    parent_security_sddl: &str,
    leaf_security_sddl: &str,
) -> Result<(Vec<OwnedHandle>, OwnedHandle), AuthorityMaintenanceError> {
    let protected_base = open_absolute_protected_base(protected_base_path, parent_security_sddl)?;
    let mut chain = vec![protected_base];
    for component in components.iter().copied() {
        let (child, created) = nt_open_relative(
            chain.last().expect("anchor exists"),
            component,
            parent_security_sddl,
            true,
            FILE_OPEN,
            DIRECTORY_READ_ACCESS | ACCESS_SYSTEM_SECURITY,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        )?;
        if created {
            return Err(AuthorityMaintenanceError(
                "authority_worker_recovery_created_path",
            ));
        }
        verify_protected_directory(&child, parent_security_sddl)?;
        chain.push(child);
    }
    let (leaf, created) = nt_open_relative(
        chain.last().expect("parent exists"),
        leaf,
        leaf_security_sddl,
        true,
        FILE_OPEN,
        DIRECTORY_READ_ACCESS | ACCESS_SYSTEM_SECURITY,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
    )?;
    if created {
        return Err(AuthorityMaintenanceError(
            "authority_worker_recovery_created_path",
        ));
    }
    verify_protected_directory(&leaf, leaf_security_sddl)?;
    Ok((chain, leaf))
}

fn open_candidate_consumption_state_chain(
    layout: &AuthorityLayout,
) -> Result<(Vec<OwnedHandle>, OwnedHandle), AuthorityMaintenanceError> {
    let state_base = open_absolute_protected_directory_with_information(
        layout.state_base(),
        STATE_DIRECTORY_SDDL,
        DIRECTORY_READ_ACCESS,
        CANDIDATE_SECURITY_INFORMATION,
    )?;
    let chain = vec![state_base];
    let (state_version_root, created) = nt_open_relative(
        chain.last().expect("state base exists"),
        "v1",
        STATE_DIRECTORY_SDDL,
        true,
        FILE_OPEN,
        DIRECTORY_READ_ACCESS,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
    )?;
    if created {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_consumption_state_created_path",
        ));
    }
    verify_candidate_protected_directory(&state_version_root, STATE_DIRECTORY_SDDL)?;
    require_expected_leaf_path(&state_version_root, layout.state_root())?;
    Ok((chain, state_version_root))
}

fn open_absolute_protected_base(
    path: &Path,
    security_sddl: &str,
) -> Result<OwnedHandle, AuthorityMaintenanceError> {
    open_absolute_protected_directory(
        path,
        security_sddl,
        DIRECTORY_READ_ACCESS | ACCESS_SYSTEM_SECURITY,
    )
}

fn open_absolute_protected_directory(
    path: &Path,
    security_sddl: &str,
    desired_access: u32,
) -> Result<OwnedHandle, AuthorityMaintenanceError> {
    open_absolute_protected_directory_with_information(
        path,
        security_sddl,
        desired_access,
        SECURITY_INFORMATION,
    )
}

fn open_absolute_protected_directory_with_information(
    path: &Path,
    security_sddl: &str,
    desired_access: u32,
    security_information: u32,
) -> Result<OwnedHandle, AuthorityMaintenanceError> {
    if !path.is_absolute() || !path_is_local(path) {
        return Err(AuthorityMaintenanceError(
            "authority_worker_protected_base_path_invalid",
        ));
    }
    let _security_privilege = if desired_access & ACCESS_SYSTEM_SECURITY != 0 {
        Some(SecurityAuditPrivilegeGuard::enable()?)
    } else {
        None
    };
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
            "authority_worker_protected_base_open_failed",
        ));
    }
    let handle = unsafe { OwnedHandle::from_raw_handle(handle as RawHandle) };
    let identity = file_identity(&handle)?;
    if identity.attributes & FILE_ATTRIBUTE_DIRECTORY == 0
        || identity.attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_protected_base_identity_invalid",
        ));
    }
    require_expected_leaf_path(&handle, path)?;
    verify_protected_directory_with_information(&handle, security_sddl, security_information)?;
    Ok(handle)
}

fn open_absolute_protected_directory_optional(
    path: &Path,
    security_sddl: &str,
    desired_access: u32,
) -> Result<Option<OwnedHandle>, AuthorityMaintenanceError> {
    if !path.is_absolute() || !path_is_local(path) {
        return Err(AuthorityMaintenanceError(
            "authority_worker_protected_base_path_invalid",
        ));
    }
    let _security_privilege = SecurityAuditPrivilegeGuard::enable()?;
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
        let error = unsafe { GetLastError() };
        if matches!(error, ERROR_FILE_NOT_FOUND | ERROR_PATH_NOT_FOUND) {
            return Ok(None);
        }
        return Err(AuthorityMaintenanceError(
            "authority_worker_protected_base_open_failed",
        ));
    }
    let handle = unsafe { OwnedHandle::from_raw_handle(handle as RawHandle) };
    let identity = file_identity(&handle)?;
    if identity.attributes & FILE_ATTRIBUTE_DIRECTORY == 0
        || identity.attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_protected_base_identity_invalid",
        ));
    }
    require_expected_leaf_path(&handle, path)?;
    verify_protected_directory(&handle, security_sddl)?;
    Ok(Some(handle))
}

fn open_relative_file_existing(
    parent: &OwnedHandle,
    name: &str,
    security_sddl: &str,
    share_mode: u32,
    writable: bool,
) -> Result<OwnedHandle, AuthorityMaintenanceError> {
    let desired_access = if writable {
        FILE_WRITE_ONCE_ACCESS | ACCESS_SYSTEM_SECURITY
    } else {
        FILE_READ_ACCESS | ACCESS_SYSTEM_SECURITY
    };
    let (file, created) = nt_open_relative(
        parent,
        name,
        security_sddl,
        false,
        FILE_OPEN,
        desired_access,
        share_mode,
    )?;
    if created {
        return Err(AuthorityMaintenanceError(
            "authority_worker_recovery_created_path",
        ));
    }
    verify_protected_file(&file, security_sddl)?;
    Ok(file)
}

fn create_worker_binary_chain(
    layout: &AuthorityLayout,
    capsule_sha256: &[u8; 32],
) -> Result<(Vec<OwnedHandle>, OwnedHandle), AuthorityMaintenanceError> {
    let expected = layout
        .maintenance_worker_root(capsule_sha256)
        .map_err(|_| AuthorityMaintenanceError("authority_worker_path_invalid"))?;
    let (chain, leaf) = create_protected_chain(
        layout.binary_anchor(),
        &["VRCForgeEvidenceAuthority", "v1", "maintenance"],
        &hex_lower(capsule_sha256),
        BINARY_DIRECTORY_SDDL,
        BINARY_GENERATION_DIRECTORY_SDDL,
    )?;
    require_expected_leaf_path(&leaf, &expected)?;
    Ok((chain, leaf))
}

fn create_worker_state_chain(
    layout: &AuthorityLayout,
    capsule_sha256: &[u8; 32],
) -> Result<(Vec<OwnedHandle>, OwnedHandle), AuthorityMaintenanceError> {
    let expected = layout
        .maintenance_worker_state_root(capsule_sha256)
        .map_err(|_| AuthorityMaintenanceError("authority_worker_path_invalid"))?;
    let (chain, leaf) = create_protected_chain(
        layout.state_anchor(),
        &["VRCForgeEvidenceAuthority", "v1", "maintenance"],
        &hex_lower(capsule_sha256),
        STATE_DIRECTORY_SDDL,
        STATE_GENERATION_DIRECTORY_SDDL,
    )?;
    require_expected_leaf_path(&leaf, &expected)?;
    Ok((chain, leaf))
}

fn create_protected_chain(
    anchor_path: &Path,
    existing_components: &[&str],
    create_new_leaf: &str,
    parent_security_sddl: &str,
    leaf_security_sddl: &str,
) -> Result<(Vec<OwnedHandle>, OwnedHandle), AuthorityMaintenanceError> {
    let anchor = open_absolute_anchor(anchor_path)?;
    let mut chain = vec![anchor];
    for component in existing_components {
        let (child, created) = open_or_create_relative_directory(
            chain.last().expect("anchor exists"),
            component,
            parent_security_sddl,
            false,
        )?;
        verify_protected_directory(&child, parent_security_sddl)?;
        if created {
            flush_handle(
                chain.last().expect("parent exists"),
                "authority_worker_directory_parent_flush_failed",
            )?;
        }
        chain.push(child);
    }
    let (leaf, created) = open_or_create_relative_directory(
        chain.last().expect("maintenance parent exists"),
        create_new_leaf,
        leaf_security_sddl,
        true,
    )?;
    if !created {
        return Err(AuthorityMaintenanceError(
            "authority_worker_staging_path_reused",
        ));
    }
    verify_protected_directory(&leaf, leaf_security_sddl)?;
    flush_handle(
        chain.last().expect("maintenance parent exists"),
        "authority_worker_directory_parent_flush_failed",
    )?;
    Ok((chain, leaf))
}

fn open_absolute_anchor(path: &Path) -> Result<OwnedHandle, AuthorityMaintenanceError> {
    if !path.is_absolute() || !path_is_local(path) {
        return Err(AuthorityMaintenanceError(
            "authority_worker_anchor_path_invalid",
        ));
    }
    let encoded = wide_null(path);
    let handle = unsafe {
        CreateFileW(
            encoded.as_ptr(),
            DIRECTORY_CREATE_CHILD_ACCESS,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            ptr::null(),
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
            ptr::null_mut(),
        )
    };
    if handle == INVALID_HANDLE_VALUE {
        return Err(AuthorityMaintenanceError(
            "authority_worker_anchor_open_failed",
        ));
    }
    let handle = unsafe { OwnedHandle::from_raw_handle(handle as RawHandle) };
    let identity = file_identity(&handle)?;
    if identity.attributes & FILE_ATTRIBUTE_DIRECTORY == 0
        || identity.attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_anchor_identity_invalid",
        ));
    }
    Ok(handle)
}

fn open_or_create_relative_directory(
    parent: &OwnedHandle,
    name: &str,
    security_sddl: &str,
    create_new: bool,
) -> Result<(OwnedHandle, bool), AuthorityMaintenanceError> {
    nt_open_relative(
        parent,
        name,
        security_sddl,
        true,
        if create_new {
            FILE_CREATE
        } else {
            FILE_OPEN_IF
        },
        DIRECTORY_CREATE_CHILD_ACCESS | ACCESS_SYSTEM_SECURITY,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
    )
}

fn create_relative_file_exact(
    parent: &OwnedHandle,
    name: &str,
    security_sddl: &str,
    bytes: &[u8],
    share_mode: u32,
) -> Result<OwnedHandle, AuthorityMaintenanceError> {
    if bytes.is_empty() || bytes.len() > MAX_PROTECTED_RECEIPT_BYTES {
        return Err(AuthorityMaintenanceError(
            "authority_worker_staged_file_size_invalid",
        ));
    }
    let (file, created) = nt_open_relative(
        parent,
        name,
        security_sddl,
        false,
        FILE_CREATE,
        FILE_WRITE_ONCE_ACCESS | ACCESS_SYSTEM_SECURITY,
        share_mode,
    )?;
    if !created {
        return Err(AuthorityMaintenanceError(
            "authority_worker_staging_path_reused",
        ));
    }
    verify_protected_file(&file, security_sddl)?;
    write_held_file_exact(&file, bytes)?;
    flush_handle(&file, "authority_worker_staged_file_flush_failed")?;
    flush_handle(parent, "authority_worker_directory_parent_flush_failed")?;
    if read_held_file_bounded(&file, MAX_PROTECTED_RECEIPT_BYTES)? != bytes {
        return Err(AuthorityMaintenanceError(
            "authority_worker_staged_file_readback_mismatch",
        ));
    }
    Ok(file)
}

fn persist_sealed_receipt<T>(
    parent: &OwnedHandle,
    name: &str,
    bytes: &[u8],
    receipt: T,
) -> Result<NativePersistedReceipt<T>, AuthorityMaintenanceError> {
    let receipt_file =
        create_relative_file_exact(parent, name, STATE_FILE_SDDL, bytes, FILE_SHARE_READ)?;
    let identity = file_identity(&receipt_file)?;
    verify_exact_file_identity(&identity, bytes.len() as u64)?;
    let bytes_sha256: [u8; 32] = Sha256::digest(bytes).into();
    if bytes_sha256.iter().all(|value| *value == 0)
        || read_held_file_bounded(&receipt_file, MAX_PROTECTED_RECEIPT_BYTES)? != bytes
        || file_identity(&receipt_file)? != identity
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_receipt_persistence_mismatch",
        ));
    }
    Ok(NativePersistedReceipt {
        _receipt_file: receipt_file,
        receipt,
        file_identity: identity,
        bytes_sha256,
    })
}

fn persist_or_reopen_sealed_receipt<T>(
    parent: &OwnedHandle,
    name: &str,
    bytes: &[u8],
    receipt: T,
) -> Result<NativePersistedReceipt<T>, AuthorityMaintenanceError> {
    if bytes.is_empty() || bytes.len() > MAX_PROTECTED_RECEIPT_BYTES {
        return Err(AuthorityMaintenanceError(
            "authority_worker_receipt_persistence_invalid",
        ));
    }
    let (receipt_file, created) = nt_open_relative(
        parent,
        name,
        STATE_FILE_SDDL,
        false,
        FILE_OPEN_IF,
        FILE_WRITE_ONCE_ACCESS | ACCESS_SYSTEM_SECURITY,
        FILE_SHARE_READ,
    )?;
    verify_protected_file(&receipt_file, STATE_FILE_SDDL)?;
    if created {
        write_held_file_exact(&receipt_file, bytes)?;
        flush_handle(&receipt_file, "authority_worker_receipt_flush_failed")?;
        flush_handle(parent, "authority_worker_receipt_parent_flush_failed")?;
    }
    let identity = file_identity(&receipt_file)?;
    verify_exact_file_identity(&identity, bytes.len() as u64)?;
    let bytes_sha256: [u8; 32] = Sha256::digest(bytes).into();
    if bytes_sha256.iter().all(|value| *value == 0)
        || read_held_file_bounded(&receipt_file, MAX_PROTECTED_RECEIPT_BYTES)? != bytes
        || file_identity(&receipt_file)? != identity
    {
        return Err(AuthorityMaintenanceError(if created {
            "authority_worker_receipt_persistence_mismatch"
        } else {
            "authority_worker_receipt_name_reuse_mismatch"
        }));
    }
    Ok(NativePersistedReceipt {
        _receipt_file: receipt_file,
        receipt,
        file_identity: identity,
        bytes_sha256,
    })
}

fn require_worker_receipt_path(
    layout: &AuthorityLayout,
    capsule: &MaintenanceWorkerCapsule,
    file: &OwnedHandle,
    name: &str,
) -> Result<(), AuthorityMaintenanceError> {
    require_expected_leaf_path(
        file,
        &layout
            .maintenance_worker_state_root(&capsule.digest()?)
            .map_err(|_| AuthorityMaintenanceError("authority_worker_path_invalid"))?
            .join(name),
    )
}

fn open_worker_receipt_file(
    layout: &AuthorityLayout,
    state_directory: &OwnedHandle,
    capsule: &MaintenanceWorkerCapsule,
    name: &str,
) -> Result<(OwnedHandle, Vec<u8>), AuthorityMaintenanceError> {
    let file = open_relative_file_existing(
        state_directory,
        name,
        STATE_FILE_SDDL,
        FILE_SHARE_READ,
        false,
    )?;
    require_worker_receipt_path(layout, capsule, &file, name)?;
    let bytes = read_held_file_bounded(&file, MAX_PROTECTED_RECEIPT_BYTES)?;
    Ok((file, bytes))
}

fn create_bytes_relative_file(
    parent: &OwnedHandle,
    name: &str,
    security_sddl: &str,
    payload: &'static str,
    bytes: &[u8],
    expected: AuthorityPayloadDigest,
) -> Result<(OwnedHandle, WorkerBootstrapStagedFileBinding), AuthorityMaintenanceError> {
    if Sha256::digest(bytes).as_slice() != expected.sha256()
        || bytes.len() as u64 != expected.byte_length()
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_staged_file_content_invalid",
        ));
    }
    let file = create_relative_file_exact(parent, name, security_sddl, bytes, FILE_SHARE_READ)?;
    let identity = file_identity(&file)?;
    verify_exact_file_identity(&identity, expected.byte_length())?;
    let binding = WorkerBootstrapStagedFileBinding::from_observed(
        payload,
        match payload {
            "capsule" => CAPSULE_FILE_NAME,
            _ => name,
        },
        *expected.sha256(),
        expected.byte_length(),
        identity.volume_serial,
        identity.file_id,
        worker_bootstrap_file_readback_receipt(
            payload,
            expected.sha256(),
            expected.byte_length(),
            identity.volume_serial,
            &identity.file_id,
        ),
    );
    Ok((file, binding))
}

fn copy_source_to_relative_file(
    parent: &OwnedHandle,
    name: &str,
    security_sddl: &str,
    payload: &'static str,
    source: HANDLE,
    expected: super::worker::WorkerPayloadSourceExpectation,
) -> Result<(OwnedHandle, WorkerBootstrapStagedFileBinding), AuthorityMaintenanceError> {
    validate_source_handle(source, expected)?;
    let (destination, created) = nt_open_relative(
        parent,
        name,
        security_sddl,
        false,
        FILE_CREATE,
        FILE_WRITE_ONCE_ACCESS | ACCESS_SYSTEM_SECURITY,
        FILE_SHARE_READ,
    )?;
    if !created {
        return Err(AuthorityMaintenanceError(
            "authority_worker_staging_path_reused",
        ));
    }
    verify_protected_file(&destination, security_sddl)?;
    seek_handle(source, 0)?;
    let source_before = raw_file_identity(source)?;
    let mut copied = 0u64;
    let mut source_digest = Sha256::new();
    let mut buffer = [0u8; MAX_COPY_BUFFER_BYTES];
    loop {
        let count = read_handle(source, &mut buffer)?;
        if count == 0 {
            break;
        }
        copied = copied
            .checked_add(count as u64)
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_source_copy_size_invalid",
            ))?;
        source_digest.update(&buffer[..count]);
        write_handle(destination.as_raw_handle().cast(), &buffer[..count])?;
    }
    seek_handle(source, 0)?;
    let source_after = raw_file_identity(source)?;
    let source_sha256: [u8; 32] = source_digest.finalize().into();
    if source_before != source_after
        || source_after.volume_serial != expected.volume_serial
        || source_after.file_id != expected.file_id
        || source_after.link_count != 1
        || source_after.attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
        || copied != expected.descriptor.byte_length()
        || source_sha256 != *expected.descriptor.sha256()
        || source_full_readback_receipt(
            &expected.descriptor,
            source_after.volume_serial,
            &source_after.file_id,
            source_after.link_count,
        ) != expected.full_readback_receipt_sha256
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_source_changed_during_copy",
        ));
    }
    flush_handle(&destination, "authority_worker_staged_file_flush_failed")?;
    flush_handle(parent, "authority_worker_directory_parent_flush_failed")?;
    let destination_identity = file_identity(&destination)?;
    verify_exact_file_identity(&destination_identity, expected.descriptor.byte_length())?;
    let destination_digest = hash_held_file(&destination, expected.descriptor.byte_length())?;
    if destination_digest != *expected.descriptor.sha256()
        || file_identity(&destination)? != destination_identity
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_staged_file_readback_mismatch",
        ));
    }
    let binding = WorkerBootstrapStagedFileBinding::from_observed(
        payload,
        WORKER_EXECUTABLE_NAME,
        destination_digest,
        expected.descriptor.byte_length(),
        destination_identity.volume_serial,
        destination_identity.file_id,
        worker_bootstrap_file_readback_receipt(
            payload,
            &destination_digest,
            expected.descriptor.byte_length(),
            destination_identity.volume_serial,
            &destination_identity.file_id,
        ),
    );
    Ok((destination, binding))
}

fn copy_durable_source_to_relative_file(
    parent: &OwnedHandle,
    capsule: &MaintenanceWorkerCapsule,
    kind: StagedPayloadKind,
    source: &OwnedHandle,
) -> Result<(OwnedHandle, DurableStagedPayloadBinding), AuthorityMaintenanceError> {
    let expected = capsule.payload_source_expectation(kind)?;
    let source_handle = source.as_raw_handle().cast();
    validate_source_handle(source_handle, expected)?;
    let relative_name = kind.staging_relative_name(capsule);
    let (destination, created) = nt_open_relative(
        parent,
        &relative_name,
        STATE_FILE_SDDL,
        false,
        FILE_CREATE,
        FILE_WRITE_ONCE_ACCESS | ACCESS_SYSTEM_SECURITY,
        FILE_SHARE_READ,
    )?;
    if !created {
        return Err(AuthorityMaintenanceError(
            "authority_worker_staging_path_reused",
        ));
    }
    verify_protected_file(&destination, STATE_FILE_SDDL)?;

    seek_handle(source_handle, 0)?;
    let source_before = raw_file_identity(source_handle)?;
    let mut copied = 0u64;
    let mut source_digest = Sha256::new();
    let mut buffer = [0u8; MAX_COPY_BUFFER_BYTES];
    loop {
        let count = read_handle(source_handle, &mut buffer)?;
        if count == 0 {
            break;
        }
        copied = copied
            .checked_add(count as u64)
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_source_copy_size_invalid",
            ))?;
        source_digest.update(&buffer[..count]);
        write_handle(destination.as_raw_handle().cast(), &buffer[..count])?;
    }
    seek_handle(source_handle, 0)?;
    let source_after_copy = raw_file_identity(source_handle)?;
    let source_sha256: [u8; 32] = source_digest.finalize().into();
    if source_before != source_after_copy
        || source_after_copy.volume_serial != expected.volume_serial
        || source_after_copy.file_id != expected.file_id
        || source_after_copy.link_count != 1
        || source_after_copy.attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
        || copied != expected.descriptor.byte_length()
        || source_sha256 != *expected.descriptor.sha256()
        || source_full_readback_receipt(
            &expected.descriptor,
            source_after_copy.volume_serial,
            &source_after_copy.file_id,
            source_after_copy.link_count,
        ) != expected.full_readback_receipt_sha256
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_source_changed_during_copy",
        ));
    }

    flush_handle(&destination, "authority_worker_staged_file_flush_failed")?;
    flush_handle(parent, "authority_worker_directory_parent_flush_failed")?;
    let destination_identity = file_identity(&destination)?;
    verify_exact_file_identity(&destination_identity, expected.descriptor.byte_length())?;
    let destination_digest = hash_held_file(&destination, expected.descriptor.byte_length())?;
    if destination_digest != *expected.descriptor.sha256()
        || file_identity(&destination)? != destination_identity
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_staged_file_readback_mismatch",
        ));
    }
    validate_source_handle(source_handle, expected)?;
    let binding = DurableStagedPayloadBinding::from_observed(
        kind,
        capsule,
        expected.volume_serial,
        expected.file_id,
        expected.full_readback_receipt_sha256,
        destination_identity.volume_serial,
        destination_identity.file_id,
    )?;
    binding.validate_reopened_file(
        kind,
        capsule,
        destination_identity.volume_serial,
        destination_identity.file_id,
        destination_identity.byte_length,
        destination_digest,
    )?;
    Ok((destination, binding))
}

fn validate_source_handle(
    handle: HANDLE,
    expected: super::worker::WorkerPayloadSourceExpectation,
) -> Result<(), AuthorityMaintenanceError> {
    if handle.is_null() || handle == INVALID_HANDLE_VALUE {
        return Err(AuthorityMaintenanceError(
            "authority_worker_source_handle_invalid",
        ));
    }
    let before = raw_file_identity(handle)?;
    verify_exact_file_identity(&before, expected.descriptor.byte_length())?;
    let digest = hash_raw_handle(handle, expected.descriptor.byte_length())?;
    let after = raw_file_identity(handle)?;
    if before != after
        || after.volume_serial != expected.volume_serial
        || after.file_id != expected.file_id
        || digest != *expected.descriptor.sha256()
        || source_full_readback_receipt(
            &expected.descriptor,
            after.volume_serial,
            &after.file_id,
            after.link_count,
        ) != expected.full_readback_receipt_sha256
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_source_handle_binding_mismatch",
        ));
    }
    Ok(())
}

fn nt_open_relative(
    parent: &OwnedHandle,
    name: &str,
    security_sddl: &str,
    directory: bool,
    disposition: u32,
    desired_access: u32,
    share_mode: u32,
) -> Result<(OwnedHandle, bool), AuthorityMaintenanceError> {
    validate_relative_name(name)?;
    let _security_privilege = if desired_access & ACCESS_SYSTEM_SECURITY != 0 {
        Some(SecurityAuditPrivilegeGuard::enable()?)
    } else {
        None
    };
    let descriptor = SecurityDescriptor::from_sddl(security_sddl)?;
    let mut name_words = name.encode_utf16().collect::<Vec<_>>();
    let name_bytes = name_words
        .len()
        .checked_mul(2)
        .and_then(|value| u16::try_from(value).ok())
        .ok_or(AuthorityMaintenanceError(
            "authority_worker_relative_name_invalid",
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
        Attributes: OBJ_CASE_INSENSITIVE as u32,
        SecurityDescriptor: descriptor.0.cast(),
        SecurityQualityOfService: ptr::null(),
    };
    let mut status_block = unsafe { zeroed::<IO_STATUS_BLOCK>() };
    let mut handle = ptr::null_mut();
    let options = FILE_SYNCHRONOUS_IO_NONALERT
        | FILE_OPEN_REPARSE_POINT
        | FILE_WRITE_THROUGH
        | if directory {
            FILE_DIRECTORY_FILE
        } else {
            FILE_NON_DIRECTORY_FILE
        };
    let status = unsafe {
        NtCreateFile(
            &mut handle,
            desired_access,
            &attributes,
            &mut status_block,
            ptr::null(),
            if directory { 0 } else { FILE_ATTRIBUTE_NORMAL },
            share_mode,
            disposition,
            options,
            ptr::null(),
            0,
        )
    };
    if status < 0 || handle.is_null() || handle == INVALID_HANDLE_VALUE {
        return Err(AuthorityMaintenanceError(if directory {
            "authority_worker_relative_directory_open_failed"
        } else {
            "authority_worker_relative_file_create_failed"
        }));
    }
    let created = match status_block.Information {
        FILE_CREATED_INFORMATION => true,
        FILE_OPENED_INFORMATION => false,
        _ => {
            unsafe { drop(OwnedHandle::from_raw_handle(handle as RawHandle)) };
            return Err(AuthorityMaintenanceError(
                "authority_worker_relative_create_result_invalid",
            ));
        }
    };
    if disposition == FILE_CREATE && !created || disposition == FILE_OPEN && created {
        unsafe { drop(OwnedHandle::from_raw_handle(handle as RawHandle)) };
        return Err(AuthorityMaintenanceError(
            "authority_worker_relative_create_result_invalid",
        ));
    }
    Ok((
        unsafe { OwnedHandle::from_raw_handle(handle as RawHandle) },
        created,
    ))
}

fn nt_open_relative_optional(
    parent: &OwnedHandle,
    name: &str,
    security_sddl: &str,
    directory: bool,
    desired_access: u32,
    share_mode: u32,
) -> Result<Option<OwnedHandle>, AuthorityMaintenanceError> {
    validate_relative_name(name)?;
    let _security_privilege = if desired_access & ACCESS_SYSTEM_SECURITY != 0 {
        Some(SecurityAuditPrivilegeGuard::enable()?)
    } else {
        None
    };
    let descriptor = SecurityDescriptor::from_sddl(security_sddl)?;
    let mut name_words = name.encode_utf16().collect::<Vec<_>>();
    let name_bytes = name_words
        .len()
        .checked_mul(2)
        .and_then(|value| u16::try_from(value).ok())
        .ok_or(AuthorityMaintenanceError(
            "authority_worker_relative_name_invalid",
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
        Attributes: OBJ_CASE_INSENSITIVE as u32,
        SecurityDescriptor: descriptor.0.cast(),
        SecurityQualityOfService: ptr::null(),
    };
    let mut status_block = unsafe { zeroed::<IO_STATUS_BLOCK>() };
    let mut handle = ptr::null_mut();
    let options = FILE_SYNCHRONOUS_IO_NONALERT
        | FILE_OPEN_REPARSE_POINT
        | FILE_WRITE_THROUGH
        | if directory {
            FILE_DIRECTORY_FILE
        } else {
            FILE_NON_DIRECTORY_FILE
        };
    let status = unsafe {
        NtCreateFile(
            &mut handle,
            desired_access,
            &attributes,
            &mut status_block,
            ptr::null(),
            if directory { 0 } else { FILE_ATTRIBUTE_NORMAL },
            share_mode,
            FILE_OPEN,
            options,
            ptr::null(),
            0,
        )
    };
    if matches!(
        status,
        STATUS_OBJECT_NAME_NOT_FOUND | STATUS_OBJECT_PATH_NOT_FOUND | STATUS_NO_SUCH_FILE
    ) {
        return Ok(None);
    }
    if status < 0 || handle.is_null() || handle == INVALID_HANDLE_VALUE {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_consumption_tombstone_open_failed",
        ));
    }
    if status_block.Information != FILE_OPENED_INFORMATION {
        unsafe { drop(OwnedHandle::from_raw_handle(handle as RawHandle)) };
        return Err(AuthorityMaintenanceError(
            "authority_candidate_consumption_tombstone_open_result_invalid",
        ));
    }
    Ok(Some(unsafe {
        OwnedHandle::from_raw_handle(handle as RawHandle)
    }))
}

fn create_candidate_consumption_file(
    parent: &OwnedHandle,
    name: &str,
) -> Result<OwnedHandle, AuthorityMaintenanceError> {
    validate_relative_name(name)?;
    let descriptor = SecurityDescriptor::from_sddl_projection(
        CANDIDATE_CONSUMPTION_FILE_SDDL,
        CANDIDATE_SECURITY_INFORMATION,
    )?;
    let mut name_words = name.encode_utf16().collect::<Vec<_>>();
    let name_bytes = name_words
        .len()
        .checked_mul(2)
        .and_then(|value| u16::try_from(value).ok())
        .ok_or(AuthorityMaintenanceError(
            "authority_worker_relative_name_invalid",
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
        Attributes: OBJ_CASE_INSENSITIVE as u32,
        SecurityDescriptor: descriptor.0.cast(),
        SecurityQualityOfService: ptr::null(),
    };
    let mut status_block = unsafe { zeroed::<IO_STATUS_BLOCK>() };
    let mut handle = ptr::null_mut();
    let status = unsafe {
        NtCreateFile(
            &mut handle,
            FILE_WRITE_ONCE_ACCESS,
            &attributes,
            &mut status_block,
            ptr::null(),
            FILE_ATTRIBUTE_NORMAL,
            FILE_SHARE_READ,
            FILE_CREATE,
            FILE_SYNCHRONOUS_IO_NONALERT
                | FILE_OPEN_REPARSE_POINT
                | FILE_WRITE_THROUGH
                | FILE_NON_DIRECTORY_FILE,
            ptr::null(),
            0,
        )
    };
    if status == STATUS_OBJECT_NAME_COLLISION {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_consumption_already_recorded",
        ));
    }
    if status < 0 || handle.is_null() || handle == INVALID_HANDLE_VALUE {
        return Err(AuthorityMaintenanceError(
            "authority_candidate_consumption_tombstone_create_failed",
        ));
    }
    if status_block.Information != FILE_CREATED_INFORMATION {
        unsafe { drop(OwnedHandle::from_raw_handle(handle as RawHandle)) };
        return Err(AuthorityMaintenanceError(
            "authority_candidate_consumption_tombstone_create_result_invalid",
        ));
    }
    Ok(unsafe { OwnedHandle::from_raw_handle(handle as RawHandle) })
}

fn open_relative_optional_for_delete(
    parent: &OwnedHandle,
    name: &str,
    directory: bool,
) -> Result<Option<OwnedHandle>, AuthorityMaintenanceError> {
    validate_relative_name(name)?;
    let _security_privilege = SecurityAuditPrivilegeGuard::enable()?;
    let mut name_words = name.encode_utf16().collect::<Vec<_>>();
    let name_bytes = name_words
        .len()
        .checked_mul(2)
        .and_then(|value| u16::try_from(value).ok())
        .ok_or(AuthorityMaintenanceError(
            "authority_worker_relative_name_invalid",
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
        Attributes: OBJ_CASE_INSENSITIVE as u32,
        SecurityDescriptor: ptr::null(),
        SecurityQualityOfService: ptr::null(),
    };
    let mut status_block = unsafe { zeroed::<IO_STATUS_BLOCK>() };
    let mut handle = ptr::null_mut();
    let desired_access = DELETE
        | FILE_READ_ATTRIBUTES
        | READ_CONTROL
        | SYNCHRONIZE
        | ACCESS_SYSTEM_SECURITY
        | if directory {
            FILE_LIST_DIRECTORY | FILE_TRAVERSE
        } else {
            FILE_READ_DATA | FILE_READ_EA
        };
    let options = FILE_SYNCHRONOUS_IO_NONALERT
        | FILE_OPEN_REPARSE_POINT
        | FILE_WRITE_THROUGH
        | if directory {
            FILE_DIRECTORY_FILE
        } else {
            FILE_NON_DIRECTORY_FILE
        };
    let status = unsafe {
        NtCreateFile(
            &mut handle,
            desired_access,
            &attributes,
            &mut status_block,
            ptr::null(),
            if directory { 0 } else { FILE_ATTRIBUTE_NORMAL },
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            FILE_OPEN,
            options,
            ptr::null(),
            0,
        )
    };
    if matches!(
        status,
        STATUS_OBJECT_NAME_NOT_FOUND | STATUS_OBJECT_PATH_NOT_FOUND | STATUS_NO_SUCH_FILE
    ) {
        return Ok(None);
    }
    if status < 0 || handle.is_null() || handle == INVALID_HANDLE_VALUE {
        return Err(AuthorityMaintenanceError(
            "authority_worker_partial_staging_open_failed",
        ));
    }
    if status_block.Information != FILE_OPENED_INFORMATION {
        unsafe { drop(OwnedHandle::from_raw_handle(handle as RawHandle)) };
        return Err(AuthorityMaintenanceError(
            "authority_worker_partial_staging_open_result_invalid",
        ));
    }
    Ok(Some(unsafe {
        OwnedHandle::from_raw_handle(handle as RawHandle)
    }))
}

fn remove_known_partial_stage_entry(
    stage_directory: &OwnedHandle,
    name: &str,
    expected_path: &Path,
) -> Result<(), AuthorityMaintenanceError> {
    let Some(file) = open_relative_optional_for_delete(stage_directory, name, false)? else {
        return Ok(());
    };
    verify_protected_file(&file, STATE_FILE_SDDL)?;
    require_expected_leaf_path(&file, expected_path)?;
    mark_delete_on_close(&file)?;
    drop(file);
    if open_relative_optional_for_delete(stage_directory, name, false)?.is_some() {
        return Err(AuthorityMaintenanceError(
            "authority_worker_partial_staging_residue",
        ));
    }
    Ok(())
}

fn mark_delete_on_close(handle: &OwnedHandle) -> Result<(), AuthorityMaintenanceError> {
    let disposition = FILE_DISPOSITION_INFORMATION_EX {
        Flags: FILE_DISPOSITION_DELETE,
    };
    let mut status_block = unsafe { zeroed::<IO_STATUS_BLOCK>() };
    let status = unsafe {
        NtSetInformationFile(
            handle.as_raw_handle().cast(),
            &mut status_block,
            (&disposition as *const FILE_DISPOSITION_INFORMATION_EX).cast(),
            size_of::<FILE_DISPOSITION_INFORMATION_EX>() as u32,
            FileDispositionInformationEx,
        )
    };
    if status < 0 {
        return Err(AuthorityMaintenanceError(
            "authority_worker_partial_staging_delete_failed",
        ));
    }
    Ok(())
}

fn partial_staging_absence_readback(
    capsule: &MaintenanceWorkerCapsule,
    intent: &WorkerSourceStagingIntentReceipt,
) -> Result<[u8; 32], AuthorityMaintenanceError> {
    let mut digest = Sha256::new();
    digest.update(b"vrcforge-authority-worker-partial-stage-absence-v1\0");
    digest.update(capsule.digest()?);
    digest.update(intent.digest()?);
    digest.update([1]);
    Ok(digest.finalize().into())
}

fn terminal_staging_absence_readback(
    capsule: &MaintenanceWorkerCapsule,
    staging: &DurableSourceStagingReceipt,
    terminal: &MaintenanceWorkerJournalRecord,
    disposition: super::worker::WorkerStagingTerminalDisposition,
) -> Result<[u8; 32], AuthorityMaintenanceError> {
    let disposition_code = match disposition {
        super::worker::WorkerStagingTerminalDisposition::RemovedAfterCommit => 1u8,
        super::worker::WorkerStagingTerminalDisposition::RemovedAfterRollback => 2u8,
        super::worker::WorkerStagingTerminalDisposition::SealedContained => {
            return Err(AuthorityMaintenanceError(
                "authority_worker_source_stage_resolution_invalid",
            ))
        }
    };
    let mut digest = Sha256::new();
    digest.update(b"vrcforge-authority-worker-terminal-stage-absence-v1\0");
    digest.update(capsule.digest()?);
    digest.update(staging.digest()?);
    digest.update(terminal.phase_receipt_sha256()?);
    digest.update([disposition_code, 1]);
    Ok(digest.finalize().into())
}

struct SecurityAuditPrivilegeGuard {
    token: OwnedHandle,
    previous: TOKEN_PRIVILEGES,
    previous_length: u32,
}

impl SecurityAuditPrivilegeGuard {
    fn enable() -> Result<Self, AuthorityMaintenanceError> {
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
            return Err(AuthorityMaintenanceError(
                "authority_worker_security_privilege_open_failed",
            ));
        }
        let token = unsafe { OwnedHandle::from_raw_handle(token as RawHandle) };
        let privilege_name = SECURITY_AUDIT_PRIVILEGE
            .encode_utf16()
            .chain(std::iter::once(0))
            .collect::<Vec<_>>();
        let mut luid = LUID {
            LowPart: 0,
            HighPart: 0,
        };
        if unsafe { LookupPrivilegeValueW(ptr::null(), privilege_name.as_ptr(), &mut luid) } == 0 {
            return Err(AuthorityMaintenanceError(
                "authority_worker_security_privilege_lookup_failed",
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
        unsafe { SetLastError(0) };
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
            || previous_length < size_of::<TOKEN_PRIVILEGES>() as u32
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_security_privilege_enable_failed",
            ));
        }
        Ok(Self {
            token,
            previous,
            previous_length,
        })
    }
}

impl Drop for SecurityAuditPrivilegeGuard {
    fn drop(&mut self) {
        if self.previous_length >= size_of::<TOKEN_PRIVILEGES>() as u32 {
            unsafe {
                AdjustTokenPrivileges(
                    self.token.as_raw_handle().cast(),
                    0,
                    &self.previous,
                    0,
                    ptr::null_mut(),
                    ptr::null_mut(),
                );
            }
        }
    }
}

fn verify_protected_directory(
    handle: &OwnedHandle,
    security_sddl: &str,
) -> Result<(), AuthorityMaintenanceError> {
    verify_protected_directory_with_information(handle, security_sddl, SECURITY_INFORMATION)
}

fn verify_candidate_protected_directory(
    handle: &OwnedHandle,
    security_sddl: &str,
) -> Result<(), AuthorityMaintenanceError> {
    verify_protected_directory_with_information(
        handle,
        security_sddl,
        CANDIDATE_SECURITY_INFORMATION,
    )
}

fn verify_protected_directory_with_information(
    handle: &OwnedHandle,
    security_sddl: &str,
    security_information: u32,
) -> Result<(), AuthorityMaintenanceError> {
    let identity = file_identity(handle)?;
    if identity.attributes & FILE_ATTRIBUTE_DIRECTORY == 0
        || identity.attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_directory_identity_invalid",
        ));
    }
    verify_handle_security_information(handle, security_sddl, security_information)
}

fn verify_protected_file(
    handle: &OwnedHandle,
    security_sddl: &str,
) -> Result<(), AuthorityMaintenanceError> {
    verify_protected_file_with_information(handle, security_sddl, SECURITY_INFORMATION)
}

fn verify_candidate_protected_file(
    handle: &OwnedHandle,
    security_sddl: &str,
) -> Result<(), AuthorityMaintenanceError> {
    verify_protected_file_with_information(handle, security_sddl, CANDIDATE_SECURITY_INFORMATION)
}

fn verify_protected_file_with_information(
    handle: &OwnedHandle,
    security_sddl: &str,
    security_information: u32,
) -> Result<(), AuthorityMaintenanceError> {
    let identity = file_identity(handle)?;
    if identity.attributes & FILE_ATTRIBUTE_DIRECTORY != 0
        || identity.attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
        || identity.link_count != 1
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_file_identity_invalid",
        ));
    }
    verify_handle_security_information(handle, security_sddl, security_information)
}

fn verify_handle_security_information(
    handle: &OwnedHandle,
    expected_sddl: &str,
    security_information: u32,
) -> Result<(), AuthorityMaintenanceError> {
    let expected = SecurityDescriptor::from_sddl(expected_sddl)?;
    let mut actual = ptr::null_mut();
    let status = unsafe {
        GetSecurityInfo(
            handle.as_raw_handle().cast(),
            SE_FILE_OBJECT,
            security_information,
            ptr::null_mut(),
            ptr::null_mut(),
            ptr::null_mut(),
            ptr::null_mut(),
            &mut actual,
        )
    };
    if status != 0 || actual.is_null() {
        return Err(AuthorityMaintenanceError(
            "authority_worker_security_readback_failed",
        ));
    }
    let actual = SecurityDescriptor(actual);
    if descriptor_sddl_for_information(expected.0, security_information)?
        != descriptor_sddl_for_information(actual.0, security_information)?
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_security_mismatch",
        ));
    }
    Ok(())
}

struct SecurityDescriptor(PSECURITY_DESCRIPTOR);

impl SecurityDescriptor {
    fn from_sddl(value: &str) -> Result<Self, AuthorityMaintenanceError> {
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
                "authority_worker_security_descriptor_invalid",
            ));
        }
        Ok(Self(descriptor))
    }

    fn from_sddl_projection(
        value: &str,
        security_information: u32,
    ) -> Result<Self, AuthorityMaintenanceError> {
        let full = Self::from_sddl(value)?;
        let projected = descriptor_sddl_for_information(full.0, security_information)?;
        Self::from_sddl(&projected)
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

fn descriptor_sddl_for_information(
    descriptor: PSECURITY_DESCRIPTOR,
    security_information: u32,
) -> Result<String, AuthorityMaintenanceError> {
    let mut text = ptr::null_mut::<u16>();
    let mut text_length = 0u32;
    if unsafe {
        ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor,
            SDDL_REVISION_1,
            security_information,
            &mut text,
            &mut text_length,
        )
    } == 0
        || text.is_null()
        || text_length == 0
    {
        if !text.is_null() {
            unsafe { LocalFree(text.cast()) };
        }
        return Err(AuthorityMaintenanceError(
            "authority_worker_security_readback_failed",
        ));
    }
    let mut words = unsafe { std::slice::from_raw_parts(text, text_length as usize) }.to_vec();
    unsafe { LocalFree(text.cast()) };
    let terminator = words
        .iter()
        .position(|word| *word == 0)
        .ok_or(AuthorityMaintenanceError(
            "authority_worker_security_readback_failed",
        ))?;
    if terminator == 0 || words[terminator..].iter().any(|word| *word != 0) {
        return Err(AuthorityMaintenanceError(
            "authority_worker_security_readback_failed",
        ));
    }
    words.truncate(terminator);
    String::from_utf16(&words)
        .map_err(|_| AuthorityMaintenanceError("authority_worker_security_readback_failed"))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct NativeFileIdentity {
    volume_serial: u64,
    file_id: [u8; 16],
    byte_length: u64,
    link_count: u32,
    attributes: u32,
}

fn file_identity(handle: &OwnedHandle) -> Result<NativeFileIdentity, AuthorityMaintenanceError> {
    raw_file_identity(handle.as_raw_handle().cast())
}

fn raw_file_identity(handle: HANDLE) -> Result<NativeFileIdentity, AuthorityMaintenanceError> {
    let mut information = unsafe { zeroed::<BY_HANDLE_FILE_INFORMATION>() };
    if handle.is_null()
        || handle == INVALID_HANDLE_VALUE
        || unsafe { GetFileInformationByHandle(handle, &mut information) } == 0
        || information.dwVolumeSerialNumber == 0
        || (information.nFileIndexHigh == 0 && information.nFileIndexLow == 0)
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_file_identity_unavailable",
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

fn verify_exact_file_identity(
    identity: &NativeFileIdentity,
    expected_length: u64,
) -> Result<(), AuthorityMaintenanceError> {
    if identity.byte_length != expected_length
        || identity.link_count != 1
        || identity.attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT) != 0
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_file_identity_invalid",
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

fn write_held_file_exact(
    file: &OwnedHandle,
    bytes: &[u8],
) -> Result<(), AuthorityMaintenanceError> {
    seek_handle(file.as_raw_handle().cast(), 0)?;
    write_handle(file.as_raw_handle().cast(), bytes)
}

fn append_held_file_exact(
    file: &OwnedHandle,
    expected_offset: u64,
    bytes: &[u8],
) -> Result<(), AuthorityMaintenanceError> {
    let current = file_identity(file)?;
    if bytes.is_empty()
        || current.byte_length != expected_offset
        || expected_offset > i64::MAX as u64
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_journal_append_position_mismatch",
        ));
    }
    seek_handle(file.as_raw_handle().cast(), expected_offset as i64)?;
    write_handle(file.as_raw_handle().cast(), bytes)
}

fn write_handle(handle: HANDLE, bytes: &[u8]) -> Result<(), AuthorityMaintenanceError> {
    let mut offset = 0usize;
    while offset < bytes.len() {
        let chunk = &bytes[offset..];
        let mut written = 0u32;
        if unsafe {
            WriteFile(
                handle,
                chunk.as_ptr(),
                chunk.len().min(u32::MAX as usize) as u32,
                &mut written,
                ptr::null_mut(),
            )
        } == 0
            || written == 0
            || written as usize > chunk.len()
        {
            return Err(AuthorityMaintenanceError(
                "authority_worker_staged_file_write_failed",
            ));
        }
        offset += written as usize;
    }
    Ok(())
}

fn read_handle(handle: HANDLE, buffer: &mut [u8]) -> Result<usize, AuthorityMaintenanceError> {
    let mut read = 0u32;
    if unsafe {
        ReadFile(
            handle,
            buffer.as_mut_ptr(),
            buffer.len().min(u32::MAX as usize) as u32,
            &mut read,
            ptr::null_mut(),
        )
    } == 0
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_staged_file_read_failed",
        ));
    }
    Ok(read as usize)
}

#[cfg(test)]
mod access_contract_tests {
    use super::super::bootstrap_activation::{
        CandidateActivationBinding, CandidateActivationObservation,
    };
    use super::*;
    use windows_sys::Win32::Storage::FileSystem::{
        DELETE, FILE_DELETE_CHILD, WRITE_DAC, WRITE_OWNER,
    };

    fn candidate_records() -> (CandidateCredentialRecord, CandidateCredentialRecord) {
        let observation = CandidateActivationObservation::new(
            [0x11; 32], [0x12; 32], [0x13; 32], 7, [0x14; 32], [0x15; 32], [0x16; 32], [0x17; 32],
            [0x18; 32], 919, 42_424,
        )
        .unwrap();
        let binding =
            CandidateActivationBinding::new(observation, [0x19; 32], 10_000, 20_000).unwrap();
        let armed = CandidateCredentialRecord::prepared(binding)
            .unwrap()
            .arm()
            .unwrap();
        let consumed = armed.consume().unwrap();
        (armed, consumed)
    }

    #[test]
    fn retirement_transaction_binding_rejects_candidate_publication_residue() {
        assert!(transaction_binding_recovery_is_clean(false, false));
        assert!(!transaction_binding_recovery_is_clean(false, true));
    }

    #[test]
    fn retirement_transaction_binding_rejects_torn_journal_tail() {
        assert!(!transaction_binding_recovery_is_clean(true, false));
        assert!(!transaction_binding_recovery_is_clean(true, true));
    }

    #[test]
    fn mutation_handles_exclude_delete_and_acl_takeover_rights() {
        let forbidden = DELETE | FILE_DELETE_CHILD | WRITE_DAC | WRITE_OWNER;
        assert_eq!(DIRECTORY_CREATE_CHILD_ACCESS & forbidden, 0);
        assert_eq!(FILE_WRITE_ONCE_ACCESS & forbidden, 0);
        assert_ne!(DIRECTORY_CREATE_CHILD_ACCESS & FILE_ADD_FILE, 0);
        assert_ne!(DIRECTORY_CREATE_CHILD_ACCESS & FILE_ADD_SUBDIRECTORY, 0);
        assert_ne!(FILE_WRITE_ONCE_ACCESS & FILE_WRITE_DATA, 0);
        assert_eq!(DIRECTORY_CREATE_CHILD_ACCESS, 0x0012_00af);
    }

    #[test]
    fn candidate_publication_names_and_post_publish_leases_are_disjoint_and_read_only() {
        let final_name = candidate_credential_file_name(&[0x5a; 32]).unwrap();
        let armed_staging = format!("{final_name}{CANDIDATE_PRIVATE_STAGING_SUFFIX}");
        let prepared_name = format!("{final_name}{CANDIDATE_PREPARED_SUFFIX}");
        let prepared_staging = format!("{prepared_name}{CANDIDATE_PRIVATE_STAGING_SUFFIX}");
        assert_ne!(final_name, prepared_name);
        assert_ne!(armed_staging, prepared_staging);
        assert!(armed_staging.ends_with(".json.publishing"));
        assert!(prepared_staging.ends_with(".json.prepared.publishing"));

        let forbidden_directory = FILE_ADD_FILE
            | FILE_ADD_SUBDIRECTORY
            | DELETE
            | FILE_DELETE_CHILD
            | WRITE_DAC
            | WRITE_OWNER;
        let forbidden_file = FILE_WRITE_DATA | FILE_APPEND_DATA | DELETE | WRITE_DAC | WRITE_OWNER;
        assert_eq!(CANDIDATE_READONLY_DIRECTORY_ACCESS & forbidden_directory, 0);
        assert_eq!(CANDIDATE_READONLY_FILE_ACCESS & forbidden_file, 0);
        assert_ne!(CANDIDATE_PUBLICATION_DIRECTORY_ACCESS & FILE_ADD_FILE, 0);
        assert_eq!(
            CANDIDATE_PUBLICATION_DIRECTORY_ACCESS & FILE_ADD_SUBDIRECTORY,
            0
        );
        assert_eq!(CANDIDATE_PUBLICATION_DIRECTORY_ACCESS, 0x0012_00ab);
        assert_ne!(CANDIDATE_PRIVATE_STAGING_FILE_ACCESS & DELETE, 0);
    }

    #[test]
    fn recovery_base_handles_are_read_only_and_all_protected_sddl_parses() {
        let forbidden = FILE_ADD_FILE
            | FILE_ADD_SUBDIRECTORY
            | DELETE
            | FILE_DELETE_CHILD
            | WRITE_DAC
            | WRITE_OWNER;
        assert_eq!(DIRECTORY_READ_ACCESS & forbidden, 0);
        assert_eq!(DIRECTORY_READ_ACCESS, 0x0012_00a9);

        for (sddl, expected_ace) in [
            (
                BINARY_DIRECTORY_SDDL,
                format!("(A;OICI;0x001200a9;;;{MAINTENANCE_SERVICE_SID})"),
            ),
            (
                BINARY_GENERATION_DIRECTORY_SDDL,
                format!("(A;OICI;0x001300af;;;{MAINTENANCE_SERVICE_SID})"),
            ),
            (
                BINARY_FILE_SDDL,
                format!("(A;;0x0013008f;;;{MAINTENANCE_SERVICE_SID})"),
            ),
            (
                STATE_DIRECTORY_SDDL,
                format!("(A;OICI;0x001200a9;;;{MAINTENANCE_SERVICE_SID})"),
            ),
            (
                STATE_GENERATION_DIRECTORY_SDDL,
                format!("(A;OICI;0x001300af;;;{MAINTENANCE_SERVICE_SID})"),
            ),
            (
                STATE_FILE_SDDL,
                format!("(A;;0x0013008f;;;{MAINTENANCE_SERVICE_SID})"),
            ),
            (
                WORKER_NONCE_DIRECTORY_SDDL,
                format!("(A;OICI;0x001200ab;;;{MAINTENANCE_SERVICE_SID})"),
            ),
            (
                WORKER_NONCE_FILE_SDDL,
                format!("(A;;0x0013008f;;;{MAINTENANCE_SERVICE_SID})"),
            ),
            (
                CANDIDATE_CONSUMPTION_DIRECTORY_SDDL,
                format!("(A;OICI;0x001200a9;;;{MAINTENANCE_SERVICE_SID})"),
            ),
            (
                CANDIDATE_CONSUMPTION_FILE_SDDL,
                format!("(A;;0x00120089;;;{MAINTENANCE_SERVICE_SID})"),
            ),
            (
                SEALED_NONCE_FILE_SDDL,
                format!("(A;;0x00120089;;;{MAINTENANCE_SERVICE_SID})"),
            ),
            (
                CANDIDATE_ACTIVATION_DIRECTORY_SDDL,
                format!("(A;OICI;0x001200ab;;;{MAINTENANCE_SERVICE_SID})"),
            ),
            (
                SERVICE_SECURITY_SDDL,
                format!("(A;;0x00020015;;;{MAINTENANCE_SERVICE_SID})"),
            ),
        ] {
            let descriptor = SecurityDescriptor::from_sddl(sddl).unwrap();
            assert!(!descriptor.0.is_null());
            assert!(sddl.contains(&expected_ace));
            assert_eq!(sddl.matches(&expected_ace).count(), 1);
        }
        const MAINTENANCE_SERVICE_ACCESS: u32 = 0x0002_0015;
        const SERVICE_CHANGE_CONFIG: u32 = 0x0000_0002;
        const SERVICE_STOP: u32 = 0x0000_0020;
        let forbidden = SERVICE_CHANGE_CONFIG | SERVICE_STOP | DELETE | WRITE_DAC | WRITE_OWNER;
        assert_eq!(MAINTENANCE_SERVICE_ACCESS & forbidden, 0);
        assert!(
            SERVICE_SECURITY_SDDL.contains(&format!("(A;;0x00020015;;;{MAINTENANCE_SERVICE_SID})"))
        );
        let key_descriptor = SecurityDescriptor::from_sddl(KEY_SECURITY_SDDL).unwrap();
        assert!(!key_descriptor.0.is_null());
    }

    #[test]
    fn staging_namespaces_match_the_exact_actor_policy() {
        assert!(WORKER_NONCE_DIRECTORY_SDDL.contains("(A;OICI;0x001200ab;;;BA)"));
        assert!(WORKER_NONCE_DIRECTORY_SDDL
            .contains(&format!("(A;OICI;0x001200ab;;;{MAINTENANCE_SERVICE_SID})")));
        assert!(WORKER_NONCE_FILE_SDDL.contains("(A;;0x0017008f;;;BA)"));
        assert!(WORKER_NONCE_FILE_SDDL
            .contains(&format!("(A;;0x0013008f;;;{MAINTENANCE_SERVICE_SID})")));
        assert!(CANDIDATE_CONSUMPTION_DIRECTORY_SDDL
            .contains(&format!("(A;OICI;0x001200a9;;;{MAINTENANCE_SERVICE_SID})")));
        assert!(CANDIDATE_CONSUMPTION_FILE_SDDL
            .contains(&format!("(A;;0x00120089;;;{MAINTENANCE_SERVICE_SID})")));
        assert!(CANDIDATE_ACTIVATION_DIRECTORY_SDDL
            .contains(&format!("(A;OICI;0x001200ab;;;{MAINTENANCE_SERVICE_SID})")));
        assert!(STATE_DIRECTORY_SDDL.contains("(A;OICI;0x001200af;;;BA)"));
        assert!(!WORKER_NONCE_DIRECTORY_SDDL.contains("0x001f01ff;;;BA"));
        assert!(!CANDIDATE_CONSUMPTION_DIRECTORY_SDDL.contains("0x001f01ff;;;BA"));
        assert!(!STATE_DIRECTORY_SDDL.contains("0x001f01ff;;;BA"));
    }

    #[test]
    fn security_audit_access_is_never_part_of_normal_io_masks() {
        assert_eq!(DIRECTORY_READ_ACCESS & ACCESS_SYSTEM_SECURITY, 0);
        assert_eq!(DIRECTORY_CREATE_CHILD_ACCESS & ACCESS_SYSTEM_SECURITY, 0);
        assert_eq!(FILE_READ_ACCESS & ACCESS_SYSTEM_SECURITY, 0);
        assert_eq!(FILE_WRITE_ONCE_ACCESS & ACCESS_SYSTEM_SECURITY, 0);
        assert_eq!(SECURITY_AUDIT_PRIVILEGE, "SeSecurityPrivilege");
    }

    #[test]
    fn candidate_consumption_security_projection_never_needs_sacl_privilege() {
        assert_eq!(
            CandidateConsumptionReadbackScope::FullSecurity.directory_access()
                & ACCESS_SYSTEM_SECURITY,
            ACCESS_SYSTEM_SECURITY
        );
        assert_eq!(
            CandidateConsumptionReadbackScope::FullSecurity.file_access() & ACCESS_SYSTEM_SECURITY,
            ACCESS_SYSTEM_SECURITY
        );
        assert_eq!(
            CandidateConsumptionReadbackScope::FullSecurity.security_information(),
            SECURITY_INFORMATION
        );
        assert_eq!(
            CandidateConsumptionReadbackScope::CandidateAccessible.directory_access()
                & ACCESS_SYSTEM_SECURITY,
            0
        );
        assert_eq!(
            CandidateConsumptionReadbackScope::CandidateAccessible.file_access()
                & ACCESS_SYSTEM_SECURITY,
            0
        );
        assert_eq!(
            CandidateConsumptionReadbackScope::CandidateAccessible.security_information(),
            CANDIDATE_SECURITY_INFORMATION
        );
        assert_eq!(
            CANDIDATE_SECURITY_INFORMATION & LABEL_SECURITY_INFORMATION,
            0
        );
        assert_eq!(
            CANDIDATE_SECURITY_INFORMATION & OWNER_SECURITY_INFORMATION,
            OWNER_SECURITY_INFORMATION
        );
        assert_eq!(
            CANDIDATE_SECURITY_INFORMATION & GROUP_SECURITY_INFORMATION,
            GROUP_SECURITY_INFORMATION
        );
        assert_eq!(
            CANDIDATE_SECURITY_INFORMATION & DACL_SECURITY_INFORMATION,
            DACL_SECURITY_INFORMATION
        );
        for access in [
            DIRECTORY_READ_ACCESS,
            DIRECTORY_CREATE_FILE_ACCESS,
            FILE_READ_ACCESS,
            FILE_WRITE_ONCE_ACCESS,
        ] {
            assert_eq!(access & ACCESS_SYSTEM_SECURITY, 0);
        }

        let full = SecurityDescriptor::from_sddl(CANDIDATE_CONSUMPTION_FILE_SDDL).unwrap();
        let projected =
            descriptor_sddl_for_information(full.0, CANDIDATE_SECURITY_INFORMATION).unwrap();
        assert!(projected.starts_with("O:SYG:SYD:P"));
        assert!(!projected.contains("S:"));
        let create_descriptor = SecurityDescriptor::from_sddl_projection(
            CANDIDATE_CONSUMPTION_FILE_SDDL,
            CANDIDATE_SECURITY_INFORMATION,
        )
        .unwrap();
        assert_eq!(
            descriptor_sddl_for_information(create_descriptor.0, CANDIDATE_SECURITY_INFORMATION,)
                .unwrap(),
            projected
        );
        assert!(
            descriptor_sddl_for_information(full.0, SECURITY_INFORMATION)
                .unwrap()
                .contains("S:(ML;;NW;;;HI)")
        );
    }

    #[test]
    fn candidate_consumption_projection_rejects_owner_group_and_dacl_drift() {
        let expected = SecurityDescriptor::from_sddl(CANDIDATE_CONSUMPTION_FILE_SDDL).unwrap();
        let expected =
            descriptor_sddl_for_information(expected.0, CANDIDATE_SECURITY_INFORMATION).unwrap();
        for changed in [
            CANDIDATE_CONSUMPTION_FILE_SDDL.replacen("O:SY", "O:BA", 1),
            CANDIDATE_CONSUMPTION_FILE_SDDL.replacen("G:SY", "G:BA", 1),
            CANDIDATE_CONSUMPTION_FILE_SDDL.replacen(
                "0x0013008f;;;S-1-5-80",
                "0x00120089;;;S-1-5-80",
                1,
            ),
        ] {
            let changed = SecurityDescriptor::from_sddl(&changed).unwrap();
            assert_ne!(
                descriptor_sddl_for_information(changed.0, CANDIDATE_SECURITY_INFORMATION,)
                    .unwrap(),
                expected
            );
        }
    }

    #[test]
    fn readonly_reopen_drops_writer_on_success_and_every_failure() {
        use std::{cell::RefCell, rc::Rc};

        #[derive(Debug)]
        struct Lease {
            name: &'static str,
            events: Rc<RefCell<Vec<&'static str>>>,
        }

        impl Drop for Lease {
            fn drop(&mut self) {
                self.events.borrow_mut().push(self.name);
            }
        }

        let events = Rc::new(RefCell::new(Vec::new()));
        let readonly = replace_writer_lease_after_readonly_reopen(
            Lease {
                name: "writer-dropped",
                events: events.clone(),
            },
            || {
                events.borrow_mut().push("readonly-opened");
                Ok(Lease {
                    name: "readonly-dropped",
                    events: events.clone(),
                })
            },
            |writer, readonly| {
                assert_eq!(writer.name, "writer-dropped");
                assert_eq!(readonly.name, "readonly-dropped");
                events.borrow_mut().push("binding-verified");
                Ok(())
            },
        )
        .unwrap();
        assert_eq!(
            events.borrow().as_slice(),
            ["readonly-opened", "binding-verified", "writer-dropped"]
        );
        drop(readonly);
        assert_eq!(events.borrow().last(), Some(&"readonly-dropped"));

        let failure_events = Rc::new(RefCell::new(Vec::new()));
        let error = replace_writer_lease_after_readonly_reopen(
            Lease {
                name: "writer-dropped",
                events: failure_events.clone(),
            },
            || {
                Ok(Lease {
                    name: "readonly-dropped",
                    events: failure_events.clone(),
                })
            },
            |_, _| Err(AuthorityMaintenanceError("injected-binding-mismatch")),
        )
        .unwrap_err();
        assert_eq!(error.code(), "injected-binding-mismatch");
        let failure_events = failure_events.borrow();
        assert!(failure_events.contains(&"writer-dropped"));
        assert!(failure_events.contains(&"readonly-dropped"));
    }

    #[test]
    fn candidate_tombstone_name_and_payload_are_exactly_credential_bound() {
        let (armed, consumed) = candidate_records();
        let credential_sha256 = consumed.credential_sha256().unwrap();
        assert_eq!(
            candidate_consumption_tombstone_name(&credential_sha256).unwrap(),
            format!("candidate.{}.consumed.json", hex_lower(&credential_sha256))
        );
        assert_eq!(
            candidate_consumption_tombstone_name(&[0; 32])
                .unwrap_err()
                .code(),
            "authority_candidate_consumption_credential_invalid"
        );
        let consumed_bytes = consumed.canonical_bytes().unwrap();
        validate_candidate_consumption_tombstone(&credential_sha256, &consumed_bytes).unwrap();
        assert_eq!(
            validate_candidate_consumption_tombstone(
                &credential_sha256,
                &armed.canonical_bytes().unwrap(),
            )
            .unwrap_err()
            .code(),
            "authority_candidate_consumption_tombstone_binding_mismatch"
        );
        let mut wrong_digest = credential_sha256;
        wrong_digest[0] ^= 1;
        assert_eq!(
            validate_candidate_consumption_tombstone(&wrong_digest, &consumed_bytes)
                .unwrap_err()
                .code(),
            "authority_candidate_consumption_tombstone_binding_mismatch"
        );
        let mut noncanonical = consumed_bytes;
        noncanonical.push(b'\n');
        assert_eq!(
            validate_candidate_consumption_tombstone(&credential_sha256, &noncanonical)
                .unwrap_err()
                .code(),
            "authority_candidate_consumption_tombstone_invalid"
        );
    }

    #[test]
    fn candidate_tombstone_persistence_has_no_rollback_delete_phase() {
        let mut observed = Vec::new();
        run_candidate_tombstone_persistence(|phase| {
            observed.push(phase);
            Ok(())
        })
        .unwrap();
        assert_eq!(observed, NATIVE_CANDIDATE_TOMBSTONE_PERSISTENCE_PHASES);

        for failure_index in 0..NATIVE_CANDIDATE_TOMBSTONE_PERSISTENCE_PHASES.len() {
            let mut prefix = Vec::new();
            let failure = run_candidate_tombstone_persistence(|phase| {
                prefix.push(phase);
                if prefix.len() - 1 == failure_index {
                    Err(AuthorityMaintenanceError(
                        "authority_candidate_tombstone_injected_failure",
                    ))
                } else {
                    Ok(())
                }
            })
            .unwrap_err();
            assert_eq!(
                failure.code(),
                "authority_candidate_tombstone_injected_failure"
            );
            assert_eq!(
                prefix,
                NATIVE_CANDIDATE_TOMBSTONE_PERSISTENCE_PHASES[..=failure_index]
            );
        }
    }

    #[test]
    fn hostile_candidate_reader_never_observes_empty_or_partial_final_bytes() {
        #[derive(Debug, Clone, Copy, PartialEq, Eq)]
        enum HostileRead {
            NotFound,
            Full,
        }

        let mut bytes_complete = false;
        let mut staging_durable = false;
        let mut final_visible = false;
        let mut writable_handles_open = false;
        let mut readonly_reopened = false;
        let mut observed = Vec::new();
        run_candidate_atomic_publication(|phase| {
            let before = if final_visible {
                assert!(bytes_complete && staging_durable);
                HostileRead::Full
            } else {
                HostileRead::NotFound
            };
            match phase {
                NativeCandidateAtomicPublicationPhase::CreatePrivateStaging => {
                    writable_handles_open = true;
                }
                NativeCandidateAtomicPublicationPhase::WriteExact => bytes_complete = true,
                NativeCandidateAtomicPublicationPhase::FlushStaging => {
                    assert!(bytes_complete);
                    staging_durable = true;
                }
                NativeCandidateAtomicPublicationPhase::VerifyPrivateStaging => {
                    assert!(bytes_complete && staging_durable && writable_handles_open);
                }
                NativeCandidateAtomicPublicationPhase::RenameNoReplace => {
                    assert!(bytes_complete && staging_durable && writable_handles_open);
                    final_visible = true;
                }
                NativeCandidateAtomicPublicationPhase::FlushParent => assert!(final_visible),
                NativeCandidateAtomicPublicationPhase::CloseWritableHandles => {
                    assert!(final_visible);
                    writable_handles_open = false;
                }
                NativeCandidateAtomicPublicationPhase::ReopenReadOnly => {
                    assert!(final_visible && !writable_handles_open);
                    readonly_reopened = true;
                }
                NativeCandidateAtomicPublicationPhase::VerifyPublished => {
                    assert!(readonly_reopened && !writable_handles_open);
                }
            }
            let after = if final_visible {
                assert!(bytes_complete && staging_durable);
                HostileRead::Full
            } else {
                HostileRead::NotFound
            };
            observed.push((phase, before, after));
            Ok(())
        })
        .unwrap();
        assert_eq!(
            observed
                .iter()
                .filter(|(_, _, after)| *after == HostileRead::Full)
                .map(|(phase, _, _)| *phase)
                .next(),
            Some(NativeCandidateAtomicPublicationPhase::RenameNoReplace)
        );
        assert!(observed.iter().all(|(phase, before, after)| {
            if *phase < NativeCandidateAtomicPublicationPhase::RenameNoReplace {
                *before == HostileRead::NotFound && *after == HostileRead::NotFound
            } else {
                *after == HostileRead::Full
            }
        }));
    }
}

fn read_held_file_bounded(
    file: &OwnedHandle,
    maximum: usize,
) -> Result<Vec<u8>, AuthorityMaintenanceError> {
    let identity = file_identity(file)?;
    if identity.byte_length == 0 || identity.byte_length > maximum as u64 {
        return Err(AuthorityMaintenanceError(
            "authority_worker_staged_file_size_invalid",
        ));
    }
    seek_handle(file.as_raw_handle().cast(), 0)?;
    let mut bytes = Vec::with_capacity(identity.byte_length as usize);
    let mut buffer = [0u8; MAX_COPY_BUFFER_BYTES];
    while bytes.len() < identity.byte_length as usize {
        let count = read_handle(file.as_raw_handle().cast(), &mut buffer)?;
        if count == 0 {
            break;
        }
        bytes.extend_from_slice(&buffer[..count]);
        if bytes.len() > maximum {
            return Err(AuthorityMaintenanceError(
                "authority_worker_staged_file_size_invalid",
            ));
        }
    }
    if bytes.len() as u64 != identity.byte_length || file_identity(file)? != identity {
        return Err(AuthorityMaintenanceError(
            "authority_worker_staged_file_readback_mismatch",
        ));
    }
    Ok(bytes)
}

fn hash_held_file(
    file: &OwnedHandle,
    expected_length: u64,
) -> Result<[u8; 32], AuthorityMaintenanceError> {
    hash_raw_handle(file.as_raw_handle().cast(), expected_length)
}

fn hash_raw_handle(
    handle: HANDLE,
    expected_length: u64,
) -> Result<[u8; 32], AuthorityMaintenanceError> {
    seek_handle(handle, 0)?;
    let mut digest = Sha256::new();
    let mut length = 0u64;
    let mut buffer = [0u8; MAX_COPY_BUFFER_BYTES];
    loop {
        let count = read_handle(handle, &mut buffer)?;
        if count == 0 {
            break;
        }
        length = length
            .checked_add(count as u64)
            .ok_or(AuthorityMaintenanceError(
                "authority_worker_staged_file_size_invalid",
            ))?;
        if length > expected_length {
            return Err(AuthorityMaintenanceError(
                "authority_worker_staged_file_size_invalid",
            ));
        }
        digest.update(&buffer[..count]);
    }
    seek_handle(handle, 0)?;
    if length != expected_length {
        return Err(AuthorityMaintenanceError(
            "authority_worker_staged_file_size_invalid",
        ));
    }
    Ok(digest.finalize().into())
}

fn seek_handle(handle: HANDLE, offset: i64) -> Result<(), AuthorityMaintenanceError> {
    let mut positioned = 0i64;
    if unsafe { SetFilePointerEx(handle, offset, &mut positioned, FILE_BEGIN) } == 0
        || positioned != offset
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_staged_file_seek_failed",
        ));
    }
    Ok(())
}

fn require_expected_leaf_path(
    handle: &OwnedHandle,
    expected: &Path,
) -> Result<(), AuthorityMaintenanceError> {
    if !expected.is_absolute() || !path_is_local(expected) {
        return Err(AuthorityMaintenanceError(
            "authority_worker_path_binding_invalid",
        ));
    }
    let mut words = vec![0u16; 32_768];
    let length = unsafe {
        GetFinalPathNameByHandleW(
            handle.as_raw_handle().cast(),
            words.as_mut_ptr(),
            words.len() as u32,
            0,
        )
    } as usize;
    if length == 0 || length >= words.len() {
        return Err(AuthorityMaintenanceError(
            "authority_worker_path_readback_failed",
        ));
    }
    words.truncate(length);
    if words.contains(&0) {
        return Err(AuthorityMaintenanceError(
            "authority_worker_path_readback_failed",
        ));
    }
    let actual = std::ffi::OsString::from_wide(&words)
        .to_string_lossy()
        .into_owned();
    let actual = actual
        .strip_prefix(r"\\?\UNC\")
        .map(|value| format!(r"\\{value}"))
        .or_else(|| actual.strip_prefix(r"\\?\").map(str::to_string))
        .unwrap_or(actual);
    let expected = expected.to_string_lossy();
    if !actual.eq_ignore_ascii_case(expected.as_ref()) {
        return Err(AuthorityMaintenanceError(
            "authority_worker_path_binding_invalid",
        ));
    }
    Ok(())
}

fn validate_relative_name(name: &str) -> Result<(), AuthorityMaintenanceError> {
    if name.is_empty()
        || name == "."
        || name == ".."
        || name.len() > 255
        || name.contains(['\\', '/', '\0', ':'])
    {
        return Err(AuthorityMaintenanceError(
            "authority_worker_relative_name_invalid",
        ));
    }
    Ok(())
}

fn path_is_local(path: &Path) -> bool {
    use windows_sys::Win32::Storage::FileSystem::{GetDriveTypeW, GetVolumePathNameW};
    const DRIVE_REMOTE: u32 = 4;
    let encoded = wide_null(path);
    let mut root = [0u16; 32_768];
    unsafe {
        GetVolumePathNameW(encoded.as_ptr(), root.as_mut_ptr(), root.len() as u32) != 0
            && GetDriveTypeW(root.as_ptr()) != DRIVE_REMOTE
    }
}

fn wide_null(path: &Path) -> Vec<u16> {
    path.as_os_str()
        .encode_wide()
        .chain(std::iter::once(0))
        .collect()
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
